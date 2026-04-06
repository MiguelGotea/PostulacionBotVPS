import asyncio
import random
import base64
import json
import aiosqlite
import logging
from fastapi import FastAPI, Request, Form, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime

from config import DB_PATH, DASHBOARD_PORT, API_KEY_GEMINI, GEMINI_MODEL
from scheduler import run_single_site_scan, cancel_site_scan, get_running_status

logger = logging.getLogger(__name__)

app = FastAPI(title="Jobs Dashboard")

# Montar estáticos y plantillas
app.mount("/static", StaticFiles(directory="dashboard/static"), name="static")
templates = Jinja2Templates(directory="dashboard/templates")

PORTALES = [
    # Operativos
    'tecoloco', 'computrabajo', 'opcionempleo', 'acciontrabajo', 'encuentra24', 'linkedin',
    # Nuevos (stubs — postulación pendiente)
    'magneto', 'bumeran', 'olx',
]

# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

async def _get_all_profiles(db) -> list:
    db.row_factory = aiosqlite.Row
    async with db.execute("SELECT * FROM candidate_profiles ORDER BY id") as cur:
        return await cur.fetchall()

async def _get_profile(db, profile_id: int):
    db.row_factory = aiosqlite.Row
    async with db.execute("SELECT * FROM candidate_profiles WHERE id = ?", (profile_id,)) as cur:
        return await cur.fetchone()

# ──────────────────────────────────────────────────────────────────
# RUTAS PRINCIPALES
# ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return RedirectResponse(url="/applied", status_code=302)

@app.get("/applied", response_class=HTMLResponse)
async def applied(request: Request, profile_id: int = None):
    """Vista completa de todos los jobs — con filtro de candidato."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        profiles = await _get_all_profiles(db)

        query = """
            SELECT j.*, cp.name as candidate_name
            FROM jobs j
            LEFT JOIN candidate_profiles cp ON cp.id = j.profile_id
            WHERE j.status NOT IN ('ignored')
        """
        params = []
        if profile_id:
            query += " AND j.profile_id = ?"
            params.append(profile_id)
        query += """
            ORDER BY
                CASE WHEN j.status = 'new' THEN 0 ELSE 1 END,
                COALESCE(j.date_applied, j.date_found) DESC
            LIMIT 500
        """
        async with db.execute(query, params) as cursor:
            jobs = await cursor.fetchall()

    return templates.TemplateResponse("applied.html", {
        "request": request,
        "jobs": jobs,
        "profiles": profiles,
        "active_profile_id": profile_id,
    })

@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    """Panel de Control global — estadísticas por candidato × sitio."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Stats por candidato y sitio
        async with db.execute("""
            SELECT cp.name as candidate_name, j.site,
                   COUNT(*) as total,
                   SUM(CASE WHEN j.status='applied' THEN 1 ELSE 0 END) as applied,
                   SUM(CASE WHEN j.status='no_cumple' THEN 1 ELSE 0 END) as no_cumple,
                   SUM(CASE WHEN j.status='failed' THEN 1 ELSE 0 END) as failed,
                   SUM(CASE WHEN j.status IN ('sin_departamento','departamento_no_permitido') THEN 1 ELSE 0 END) as dpto_filtrado
            FROM jobs j
            LEFT JOIN candidate_profiles cp ON cp.id = j.profile_id
            GROUP BY j.profile_id, j.site
            ORDER BY cp.name, j.site
        """) as cursor:
            stats_by_candidate_site = await cursor.fetchall()

        # Totales globales
        async with db.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) as applied,
                SUM(CASE WHEN status='no_cumple' THEN 1 ELSE 0 END) as no_cumple,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status IN ('sin_departamento','departamento_no_permitido') THEN 1 ELSE 0 END) as dpto_filtrado
            FROM jobs
        """) as cursor:
            totals = await cursor.fetchone()

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "stats": stats_by_candidate_site,
        "totals": totals,
    })

@app.post("/settings/scan-all")
async def scan_all(background_tasks: BackgroundTasks):
    """Dispara un escaneo global en todos los portales y todos los candidatos activos."""
    background_tasks.add_task(run_single_site_scan, None, None)
    return RedirectResponse(url="/settings?msg=Escaneo+global+iniciado", status_code=303)

@app.post("/settings/scan-site/{site_name}/{profile_id}")
async def scan_site(site_name: str, profile_id: int, background_tasks: BackgroundTasks):
    """Dispara un escaneo manual de un portal para un candidato en segundo plano."""
    background_tasks.add_task(run_single_site_scan, site_name, profile_id)
    return RedirectResponse(url=f"/profile?pid={profile_id}&msg=Escaneo+de+{site_name}+iniciado", status_code=303)


@app.post("/settings/stop-site/{site_name}")
async def stop_site(site_name: str):
    """
    Solicita la detención inmediata del portal indicado.
    El scheduler lo detectará en el próximo checkpoint y detendrá el escaneo/postulación,
    borrando los jobs con status='new' de ese portal.
    """
    cancel_site_scan(site_name)
    return JSONResponse({"ok": True, "site": site_name, "msg": f"Detención solicitada para {site_name}"})


@app.get("/api/status")
async def api_status():
    """Retorna el estado actual del ciclo de scraping/posting."""
    return JSONResponse(get_running_status())

@app.post("/settings/toggle-site/{site_name}")
async def toggle_site(site_name: str, request: Request):
    """Habilita o deshabilita un portal de empleo (global)."""
    form = await request.form()
    pid = form.get("pid", "")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_enabled FROM site_configs WHERE site_name = ?", (site_name,)) as cursor:
            row = await cursor.fetchone()
            if row:
                new_status = 0 if row[0] == 1 else 1
                await db.execute("UPDATE site_configs SET is_enabled = ? WHERE site_name = ?", (new_status, site_name))
                await db.commit()
    redirect = f"/profile?pid={pid}&msg=Portal+{site_name}+actualizado" if pid else "/profile"
    return RedirectResponse(url=redirect, status_code=303)


# ──────────────────────────────────────────────────────────────────
# API: Estadísticas de Monitoreo
# ──────────────────────────────────────────────────────────────────

@app.get("/api/stats/hourly")
async def api_stats_hourly(days: int = 7):
    """
    Retorna el conteo de jobs encontrados por hora del día (promedio últimos N días).
    Para la gráfica de actividad en Panel de Control.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT 
                CAST(strftime('%H', scan_date) AS INTEGER) as hour,
                SUM(jobs_found) as total_found,
                COUNT(*) as scan_count,
                ROUND(CAST(SUM(jobs_found) AS FLOAT) / NULLIF(COUNT(DISTINCT date(scan_date)), 0), 1) as avg_found
            FROM scan_log
            WHERE scan_date >= datetime('now', '-' || ? || ' days', '-6 hours')
            GROUP BY hour
            ORDER BY hour
        """, (days,)) as cursor:
            rows = await cursor.fetchall()
        
        # Stats por portal (tasa de éxito)
        async with db.execute("""
            SELECT 
                j.site,
                COUNT(*) as total,
                SUM(CASE WHEN j.status='applied' THEN 1 ELSE 0 END) as applied,
                ROUND(
                    100.0 * SUM(CASE WHEN j.status='applied' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
                    1
                ) as success_rate
            FROM jobs j
            GROUP BY j.site
            ORDER BY success_rate DESC
        """) as cursor:
            portal_stats = await cursor.fetchall()

        # Escaneos de las últimas 24h (para "actividad reciente")
        async with db.execute("""
            SELECT site, jobs_found, scan_date, errors
            FROM scan_log
            WHERE scan_date >= datetime('now', '-1 day', '-6 hours')
            ORDER BY scan_date DESC
            LIMIT 50
        """) as cursor:
            recent_scans = await cursor.fetchall()

    # Construir array de 24 horas
    hourly_map = {row['hour']: row for row in rows}
    hourly_data = [
        {
            "hour": h,
            "label": f"{h:02d}:00",
            "avg_found": hourly_map[h]['avg_found'] if h in hourly_map else 0,
            "total_found": hourly_map[h]['total_found'] if h in hourly_map else 0,
        }
        for h in range(24)
    ]

    return JSONResponse({
        "hourly": hourly_data,
        "portal_stats": [
            {
                "site": r['site'],
                "total": r['total'],
                "applied": r['applied'],
                "success_rate": r['success_rate'] or 0,
            }
            for r in portal_stats
        ],
        "recent_scans": [
            {
                "site": r['site'],
                "jobs_found": r['jobs_found'],
                "scan_date": r['scan_date'],
                "has_error": bool(r['errors']),
            }
            for r in recent_scans
        ],
        "days_analyzed": days,
    })


# ──────────────────────────────────────────────────────────────────
# REINTENTAR RECHAZADOS
# ──────────────────────────────────────────────────────────────────

REINTENTABLES = ('failed', 'no_cumple', 'sin_departamento', 'departamento_no_permitido')

@app.post("/reintentar-rechazados")
async def reintentar_rechazados(
    site: str = Form(None),
    profile_id: int = Form(None)
):
    """Resetea los jobs rechazados a 'new' para reintento, filtrado por candidato y/o portal."""
    placeholders = ",".join("?" * len(REINTENTABLES))
    params = list(REINTENTABLES)

    where_extra = ""
    if site:
        where_extra += " AND site=?"
        params.append(site)
    if profile_id:
        where_extra += " AND profile_id=?"
        params.append(profile_id)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE jobs SET status='new', error_message=NULL, date_applied=NULL "
            f"WHERE status IN ({placeholders}){where_extra}",
            params
        )
        count = db.total_changes
        await db.commit()

    msg = f"{count}+jobs+en+cola+para+reintento"
    redirect = f"/profile?pid={profile_id}&msg={msg}" if profile_id else f"/applied?msg={msg}"
    return RedirectResponse(url=redirect, status_code=303)

@app.post("/retry-failed")
async def retry_failed_compat(site: str = Form(None), profile_id: int = Form(None)):
    return await reintentar_rechazados(site, profile_id)

# ──────────────────────────────────────────────────────────────────
# ACCIONES DE JOBS
# ──────────────────────────────────────────────────────────────────

@app.post("/ignore/{job_id}")
async def ignore_job(job_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE jobs SET status = 'ignored' WHERE id = ?", (job_id,))
        await db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/force-apply/{job_id}")
async def force_apply_job(job_id: int, background_tasks: BackgroundTasks):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
            job = await cur.fetchone()
    if not job:
        return RedirectResponse(url="/applied?msg=Job+no+encontrado", status_code=303)

    background_tasks.add_task(_run_force_apply, dict(job))
    action = "Datos+actualizados" if job["status"] == "applied" else "Postulación+forzada+iniciada"
    return RedirectResponse(url=f"/applied?msg={action}", status_code=303)

async def _run_force_apply(job: dict):
    from playwright.async_api import async_playwright
    from poster.tecoloco import TecolocoPoster
    from poster.computrabajo import ComputrabajoPoster
    import re

    already_applied = (job.get("status") == "applied")
    site   = job.get("site", "")
    job_id = job["id"]
    profile_id = job.get("profile_id", 1)

    # Seleccionar el poster correcto según el sitio
    if site == "tecoloco":
        poster = TecolocoPoster()
        site_name_db = "tecoloco"
    elif site == "computrabajo":
        poster = ComputrabajoPoster()
        site_name_db = "computrabajo"
    else:
        logger.warning(f"[force-apply] Sitio '{site}' no soportado aún.")
        return

    creds = await poster.get_credentials_from_db(profile_id, site_name_db)

    async with async_playwright() as p:
        browser, context = await poster.get_browser_context(p)
        page = await context.new_page()
        try:
            if hasattr(poster, "_session_cookies") and poster._session_cookies:
                await page.context.add_cookies(poster._session_cookies)

            if already_applied:
                await page.goto(job["url"], wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(random.uniform(1.5, 2.5))
                if hasattr(poster, "_update_company_from_page"):
                    await poster._update_company_from_page(page, job_id)
                if hasattr(poster, "_read_location_from_page"):
                    await poster._read_location_from_page(page, job_id)
            else:
                result = await poster.apply(page, job["url"], creds, job_db_id=job_id)
                success, error_msg = result if isinstance(result, tuple) else (result, None)
                await poster.mark_applied(job_id, success, error_msg)
        except Exception as e:
            logger.error(f"[force-apply] id={job_id} excepción: {e}")
        finally:
            await browser.close()

# ──────────────────────────────────────────────────────────────────
# PERFIL — TABS Y GESTIÓN
# ──────────────────────────────────────────────────────────────────

@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, pid: int = None):
    """Página de gestión de perfiles — tabs por candidato."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        profiles = await _get_all_profiles(db)

        if not profiles:
            return templates.TemplateResponse("profile.html", {
                "request": request, "profiles": [], "active_profile": None,
                "profile_keywords": [], "profile_departments": [],
                "site_configs": [], "profile_credentials": [],
                "active_pid": None, "portales": PORTALES,
            })

        # Determinar perfil activo
        active_pid = pid if pid else profiles[0]['id']
        # Validar que existe
        if not any(p['id'] == active_pid for p in profiles):
            active_pid = profiles[0]['id']

        # Datos del perfil activo
        active_profile = next(p for p in profiles if p['id'] == active_pid)

        # Keywords del perfil activo
        async with db.execute(
            "SELECT * FROM profile_keywords WHERE profile_id = ? ORDER BY id",
            (active_pid,)
        ) as cursor:
            profile_keywords = [dict(kw) for kw in await cursor.fetchall()]

        # Departamentos del perfil activo
        async with db.execute(
            "SELECT * FROM profile_departments WHERE profile_id = ? ORDER BY department",
            (active_pid,)
        ) as cursor:
            profile_departments = [dict(d) for d in await cursor.fetchall()]

        # Portales globales
        async with db.execute("SELECT * FROM site_configs ORDER BY site_name") as cursor:
            site_configs = await cursor.fetchall()

        # Credenciales del perfil activo por portal
        async with db.execute(
            "SELECT * FROM profile_credentials WHERE profile_id = ? ORDER BY site_name",
            (active_pid,)
        ) as cursor:
            creds_rows = [dict(r) for r in await cursor.fetchall()]
        # Mapear a dict por site_name para fácil acceso en template
        profile_credentials = {r['site_name']: r for r in creds_rows}

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "profiles": profiles,
        "active_profile": active_profile,
        "active_pid": active_pid,
        "profile_keywords": profile_keywords,
        "profile_departments": profile_departments,
        "site_configs": site_configs,
        "profile_credentials": profile_credentials,
        "portales": PORTALES,
    })

@app.post("/profile/create")
async def create_profile():
    """Crea un nuevo candidato vacío."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO candidate_profiles (name, is_active) VALUES ('Nuevo Candidato', 1)"
        )
        new_id = cursor.lastrowid
        # Crear credenciales vacías para todos los portales
        for site in PORTALES:
            await db.execute(
                "INSERT OR IGNORE INTO profile_credentials (profile_id, site_name, email, password) VALUES (?,?,?,?)",
                (new_id, site, '', '')
            )
        # Crear departamentos por defecto (Managua habilitado)
        nicaragua_depts = [
            "Boaco", "Carazo", "Chinandega", "Chontales", "Estelí",
            "Granada", "Jinotega", "León", "Madriz", "Managua",
            "Masaya", "Matagalpa", "Nueva Segovia", "Río San Juan",
            "Rivas", "RAAN", "RAAS"
        ]
        for dept in nicaragua_depts:
            enabled = 1 if dept == "Managua" else 0
            await db.execute(
                "INSERT OR IGNORE INTO profile_departments (profile_id, department, is_enabled) VALUES (?,?,?)",
                (new_id, dept, enabled)
            )
        await db.commit()
    return RedirectResponse(url=f"/profile?pid={new_id}&msg=Nuevo+candidato+creado", status_code=303)

@app.post("/profile/delete/{profile_id}")
async def delete_profile(profile_id: int):
    """Elimina un candidato (no se puede eliminar el último)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM candidate_profiles") as cur:
            count = (await cur.fetchone())[0]
        if count <= 1:
            return RedirectResponse(url="/profile?msg=No+puedes+eliminar+el+unico+candidato", status_code=303)
        await db.execute("DELETE FROM candidate_profiles WHERE id = ?", (profile_id,))
        await db.execute("DELETE FROM profile_keywords WHERE profile_id = ?", (profile_id,))
        await db.execute("DELETE FROM profile_departments WHERE profile_id = ?", (profile_id,))
        await db.execute("DELETE FROM profile_credentials WHERE profile_id = ?", (profile_id,))
        await db.commit()
    return RedirectResponse(url="/profile?msg=Candidato+eliminado", status_code=303)

@app.post("/profile/update/{profile_id}")
async def update_profile(
    profile_id: int,
    name: str = Form(...),
    phone: str = Form(""),
    location: str = Form(""),
    birth_date: str = Form(""),
    civil_status: str = Form(""),
    address: str = Form(""),
    education: str = Form(""),
    experience: str = Form(""),
    skills: str = Form(""),
    languages: str = Form(""),
    salary_expectation: str = Form(""),
    availability: str = Form(""),
    about: str = Form(""),
    is_active: int = Form(1),
    notification_email: str = Form(""),
):
    """Actualiza un perfil de candidato."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE candidate_profiles SET
                name=?, phone=?, location=?, birth_date=?, civil_status=?, address=?,
                education=?, experience=?, skills=?, languages=?,
                salary_expectation=?, availability=?, about=?, is_active=?,
                notification_email=?
            WHERE id=?
        """, (name, phone, location, birth_date, civil_status, address,
              education, experience, skills, languages,
              salary_expectation, availability, about, is_active,
              notification_email, profile_id))
        await db.commit()
    return RedirectResponse(url=f"/profile?pid={profile_id}&msg=Perfil+actualizado", status_code=303)

@app.post("/profile/{profile_id}/credentials/update")
async def update_credentials(profile_id: int, request: Request):
    """Actualiza las credenciales de todos los portales para un candidato."""
    form = await request.form()
    async with aiosqlite.connect(DB_PATH) as db:
        for site in PORTALES:
            email = form.get(f"email_{site}", "")
            password = form.get(f"password_{site}", "")
            await db.execute("""
                INSERT INTO profile_credentials (profile_id, site_name, email, password)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(profile_id, site_name) DO UPDATE SET
                    email = excluded.email,
                    password = excluded.password
            """, (profile_id, site, email, password))
        await db.commit()
    return RedirectResponse(url=f"/profile?pid={profile_id}&msg=Credenciales+actualizadas", status_code=303)

# ──────────────────────────────────────────────────────────────────
# KEYWORDS
# ──────────────────────────────────────────────────────────────────

@app.post("/profile/keywords/{keyword_id}/toggle")
async def toggle_keyword(keyword_id: int, pid: int = Form(None)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT profile_id FROM profile_keywords WHERE id=?", (keyword_id,)) as cur:
            row = await cur.fetchone()
            profile_id = row[0] if row else (pid or 1)
        await db.execute(
            "UPDATE profile_keywords SET is_enabled = CASE WHEN is_enabled=1 THEN 0 ELSE 1 END WHERE id=?",
            (keyword_id,)
        )
        await db.commit()
    return RedirectResponse(url=f"/profile?pid={profile_id}", status_code=303)

@app.post("/profile/{profile_id}/keywords/add")
async def add_keyword(profile_id: int, keyword: str = Form(...)):
    keyword = keyword.strip().lower()
    if keyword:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO profile_keywords (profile_id, keyword, is_enabled) VALUES (?,?,1)",
                (profile_id, keyword)
            )
            await db.commit()
    return RedirectResponse(url=f"/profile?pid={profile_id}", status_code=303)

@app.post("/profile/keywords/{keyword_id}/delete")
async def delete_keyword(keyword_id: int, pid: int = Form(None)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT profile_id FROM profile_keywords WHERE id=?", (keyword_id,)) as cur:
            row = await cur.fetchone()
            profile_id = row[0] if row else (pid or 1)
        await db.execute("DELETE FROM profile_keywords WHERE id=?", (keyword_id,))
        await db.commit()
    return RedirectResponse(url=f"/profile?pid={profile_id}", status_code=303)

# ──────────────────────────────────────────────────────────────────
# DEPARTAMENTOS
# ──────────────────────────────────────────────────────────────────

@app.post("/profile/departments/{profile_id}/{department}/toggle")
async def toggle_department(profile_id: int, department: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO profile_departments (profile_id, department, is_enabled) VALUES (?,?,1)
            ON CONFLICT(profile_id, department) DO UPDATE SET
            is_enabled = CASE WHEN is_enabled=1 THEN 0 ELSE 1 END
        """, (profile_id, department))
        await db.commit()
    return RedirectResponse(url=f"/profile?pid={profile_id}", status_code=303)

# ──────────────────────────────────────────────────────────────────
# API — PARSE CV con IA
# ──────────────────────────────────────────────────────────────────

@app.post("/api/parse-cv")
async def parse_cv(file: UploadFile = File(...)):
    """
    Recibe un PDF del CV, lo envía a Gemini y retorna los campos del perfil
    extraídos en JSON para pre-rellenar el formulario de edición.
    """
    try:
        content = await file.read()
        if len(content) > 15 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="El PDF excede 15MB")

        pdf_b64 = base64.b64encode(content).decode()

        import google.generativeai as genai
        genai.configure(api_key=API_KEY_GEMINI)
        model = genai.GenerativeModel(GEMINI_MODEL)

        prompt = """Eres un asistente experto en extracción de información de CVs.
Del siguiente PDF de CV, extrae los datos del candidato y devuelve ÚNICAMENTE un JSON válido
con exactamente estas claves (sin texto adicional, sin markdown, sin explicaciones):

{
  "name": "nombre completo",
  "email": "correo electrónico",
  "phone": "teléfono",
  "location": "ciudad, país",
  "birth_date": "fecha de nacimiento",
  "civil_status": "estado civil",
  "address": "dirección completa",
  "education": "educación resumida (institución, título, años)",
  "experience": "experiencia laboral resumida",
  "skills": "habilidades técnicas y blandas separadas por coma",
  "languages": "idiomas que domina",
  "salary_expectation": "expectativa salarial si está indicada",
  "availability": "disponibilidad inmediata / tiempo completo / etc",
  "about": "resumen profesional del candidato en 2-3 oraciones"
}

Si algún campo no está en el CV, usa una cadena vacía "".
Responde SOLO el JSON, nada más."""

        response = model.generate_content([
            {'mime_type': 'application/pdf', 'data': pdf_b64},
            prompt
        ])

        raw = response.text.strip()
        # Limpiar markdown si Gemini lo pone
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)
        return JSONResponse(content={"ok": True, "data": data})

    except json.JSONDecodeError as e:
        logger.error(f"[parse-cv] JSON inválido de Gemini: {e}")
        return JSONResponse(content={"ok": False, "error": "No se pudo parsear la respuesta de IA"}, status_code=422)
    except Exception as e:
        logger.error(f"[parse-cv] Error: {e}")
        return JSONResponse(content={"ok": False, "error": str(e)}, status_code=500)

# ──────────────────────────────────────────────────────────────────
# API — STATS y SESSION COOKIES
# ──────────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'applied'") as cursor:
            total_applied = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'new'") as cursor:
            total_new = (await cursor.fetchone())[0]
    return {
        "total_applied": total_applied,
        "total_new": total_new,
        "last_check": datetime.now().isoformat()
    }

@app.post("/api/session")
async def save_session_cookies(request: Request):
    """Recibe cookies de sesión desde el navegador local."""
    body = await request.json()
    site       = body.get("site", "tecoloco")
    cookies    = body.get("cookies", "")
    note       = body.get("note", "")
    profile_id = body.get("profile_id", 1)

    if not cookies:
        raise HTTPException(status_code=400, detail="cookies vacías")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO session_cookies (profile_id, site, cookies, updated_at, note)
            VALUES (?, ?, ?, datetime('now', '-6 hours'), ?)
            ON CONFLICT(profile_id, site) DO UPDATE SET
                cookies    = excluded.cookies,
                updated_at = excluded.updated_at,
                note       = excluded.note
        """, (profile_id, site, cookies, note))
        await db.commit()

    return {"ok": True, "site": site, "profile_id": profile_id, "chars": len(cookies)}

@app.get("/api/session/{site}")
async def get_session_status(site: str, profile_id: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT site, length(cookies) as len, updated_at, note FROM session_cookies WHERE site=? AND profile_id=?",
            (site, profile_id)
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return {"status": "sin_cookies", "site": site}
    return {"status": "ok", "site": site, "chars": row["len"],
            "updated_at": row["updated_at"], "note": row["note"]}
