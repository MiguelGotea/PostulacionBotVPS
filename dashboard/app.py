import aiosqlite
import logging
from fastapi import FastAPI, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime

from config import DB_PATH, KEYWORDS, DASHBOARD_PORT
from scheduler import run_single_site_scan

logger = logging.getLogger(__name__)

app = FastAPI(title="Katty Jobs Dashboard")

# Montar estáticos y plantillas
app.mount("/static", StaticFiles(directory="dashboard/static"), name="static")
templates = Jinja2Templates(directory="dashboard/templates")

# Nota: La conexión se maneja directamente en cada ruta para evitar problemas 
# de hilos/threading con aiosqlite (RuntimeError: threads can only be started once)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, site: str = None):
    """Página principal con ofertas nuevas."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM jobs WHERE status = 'new'"
        params = []
        
        if site:
            query += " AND site = ?"
            params.append(site)
            
        query += " ORDER BY date_found DESC LIMIT 50"
        async with db.execute(query, params) as cursor:
            jobs = await cursor.fetchall()
            
        # Estadísticas rápidas
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'new'") as cursor:
            row = await cursor.fetchone()
            total_new = row[0] if row else 0

    return templates.TemplateResponse("index.html", {
        "request": request, 
        "jobs": jobs, 
        "total_new": total_new,
        "current_site": site
    })

@app.get("/applied", response_class=HTMLResponse)
async def applied(request: Request):
    """Historial de postulaciones."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM jobs 
            WHERE status NOT IN ('new', 'ignored', 'applying')
            ORDER BY date_applied DESC LIMIT 200
        """) as cursor:
            jobs = await cursor.fetchall()

    return templates.TemplateResponse("applied.html", {"request": request, "jobs": jobs})

@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    """Panel de Control global — estadísticas y mantenimiento."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT site,
                   COUNT(*) as total,
                   SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) as applied,
                   SUM(CASE WHEN status='no_cumple' THEN 1 ELSE 0 END) as no_cumple,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed,
                   SUM(CASE WHEN status IN ('sin_departamento','departamento_no_permitido') THEN 1 ELSE 0 END) as dpto_filtrado
            FROM jobs GROUP BY site
        """) as cursor:
            stats_by_site = await cursor.fetchall()

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "stats": stats_by_site,
    })

@app.post("/settings/update-app")
async def update_app_settings(
    tecoloco_salary: str = Form(...),
    tecoloco_working: str = Form(...)
):
    """Actualiza los parámetros globales de las postulaciones."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", ('tecoloco_salary', tecoloco_salary))
        await db.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", ('tecoloco_working', tecoloco_working))
        await db.commit()
    
    return RedirectResponse(url="/settings?msg=Parametros+actualizados", status_code=303)

@app.post("/settings/toggle-site/{site_name}")
async def toggle_site(site_name: str):
    """Habilita o deshabilita un portal de empleo."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_enabled FROM site_configs WHERE site_name = ?", (site_name,)) as cursor:
            row = await cursor.fetchone()
            if row:
                new_status = 0 if row[0] == 1 else 1
                await db.execute("UPDATE site_configs SET is_enabled = ? WHERE site_name = ?", (new_status, site_name))
                await db.commit()

    return RedirectResponse(url="/profile", status_code=303)

@app.post("/settings/scan-all")
async def scan_all(background_tasks: BackgroundTasks):
    """Dispara un escaneo global en todos los portales activos."""
    background_tasks.add_task(run_single_site_scan, None)
    return RedirectResponse(url="/settings?msg=Escaneo+global+iniciado", status_code=303)

@app.post("/settings/scan-site/{site_name}")
async def scan_site(site_name: str, background_tasks: BackgroundTasks):
    """Dispara un escaneo manual de un portal en segundo plano."""
    background_tasks.add_task(run_single_site_scan, site_name)
    return RedirectResponse(url="/profile?msg=Escaneo+de+" + site_name + "+iniciado", status_code=303)

@app.post("/settings/cleanup-invalid")
async def cleanup_invalid_jobs():
    """Elimina ofertas con URLs de categoría (sin ID numérico) de la base de datos."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Traer todos los jobs de tecoloco no aplicados para filtrarlos en Python
        async with db.execute(
            "SELECT id, url FROM jobs WHERE site = 'tecoloco' AND status != 'applied'"
        ) as cursor:
            rows = await cursor.fetchall()

        import re
        invalid_ids = []
        for row in rows:
            url = row['url'] or ''
            # URL válida de oferta individual: contiene un segmento numérico largo (ej: /1054667/)
            if not re.search(r'/\d{4,}/', url):
                invalid_ids.append(row['id'])

        if invalid_ids:
            placeholders = ','.join('?' * len(invalid_ids))
            await db.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", invalid_ids)
            await db.commit()

        deleted = len(invalid_ids)

    logger.info(f"Cleanup: {deleted} ofertas de categoría eliminadas de tecoloco.")
    return RedirectResponse(url=f"/settings?msg=Limpieza+completada:+{deleted}+ofertas+invalidas+eliminadas", status_code=303)


# Todos los statuses de rechazo que se pueden reintentar
REINTENTABLES = ('failed', 'no_cumple', 'sin_departamento', 'departamento_no_permitido')

@app.post("/reintentar-rechazados")
async def reintentar_rechazados(site: str = Form(None)):
    """Resetea TODOS los jobs rechazados a 'new' para reintento (cambió perfil, CV o departamentos)."""
    placeholders = ",".join(f"'{s}'" for s in REINTENTABLES) if False else ",".join("?" * len(REINTENTABLES))
    async with aiosqlite.connect(DB_PATH) as db:
        if site:
            await db.execute(
                f"UPDATE jobs SET status='new', error_message=NULL, date_applied=NULL WHERE status IN ({placeholders}) AND site=?",
                (*REINTENTABLES, site)
            )
        else:
            await db.execute(
                f"UPDATE jobs SET status='new', error_message=NULL, date_applied=NULL WHERE status IN ({placeholders})",
                REINTENTABLES
            )
        count = db.total_changes
        await db.commit()
    logger.info(f"Reintentar rechazados: {count} jobs reseteados (site={site or 'todos'})")
    msg = f"{count}+jobs+en+cola+para+reintento"
    return RedirectResponse(url=f"/applied?msg={msg}", status_code=303)

# Mantener la ruta antigua como alias para compatibilidad con formularios del template
@app.post("/retry-failed")
async def retry_failed_compat(site: str = Form(None)):
    return await reintentar_rechazados(site)

@app.post("/ignore/{job_id}")
async def ignore_job(job_id: int):
    """Marca una oferta como ignorada."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE jobs SET status = 'ignored' WHERE id = ?", (job_id,))
        await db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/apply/{job_id}")
async def force_apply(job_id: int):
    """Fuerza la postulación de una oferta específica."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE jobs SET status = 'applying' WHERE id = ?", (job_id,))
        await db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    """Página de gestión de perfiles de candidatos."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM candidate_profiles ORDER BY id") as cursor:
            profiles = await cursor.fetchall()
        # Cargar keywords por perfil
        profile_keywords = {}
        async with db.execute(
            "SELECT * FROM profile_keywords ORDER BY profile_id, id"
        ) as cursor:
            for kw in await cursor.fetchall():
                pid = kw["profile_id"]
                profile_keywords.setdefault(pid, []).append(dict(kw))
    # Cargar departamentos por perfil
    profile_departments = {}
    async with aiosqlite.connect(DB_PATH) as db2:
        db2.row_factory = aiosqlite.Row
        async with db2.execute("SELECT * FROM profile_departments ORDER BY profile_id, department") as cursor:
            for row in await cursor.fetchall():
                pid = row["profile_id"]
                profile_departments.setdefault(pid, []).append(dict(row))

    # Cargar portales (site_configs) — globales por ahora
    async with aiosqlite.connect(DB_PATH) as db3:
        db3.row_factory = aiosqlite.Row
        async with db3.execute("SELECT * FROM site_configs ORDER BY site_name") as cursor:
            site_configs = await cursor.fetchall()

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "profiles": profiles,
        "profile_keywords": profile_keywords,
        "profile_departments": profile_departments,
        "site_configs": site_configs,
    })

@app.post("/profile/keywords/{keyword_id}/toggle")
async def toggle_keyword(keyword_id: int):
    """Activa/desactiva una keyword de búsqueda."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE profile_keywords SET is_enabled = CASE WHEN is_enabled=1 THEN 0 ELSE 1 END WHERE id=?",
            (keyword_id,)
        )
        await db.commit()
    return RedirectResponse(url="/profile", status_code=303)

@app.post("/profile/departments/{profile_id}/{department}/toggle")
async def toggle_department(profile_id: int, department: str):
    """Activa/desactiva un departamento de búsqueda."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO profile_departments (profile_id, department, is_enabled) VALUES (?,?,1)
               ON CONFLICT(profile_id, department) DO UPDATE SET
               is_enabled = CASE WHEN is_enabled=1 THEN 0 ELSE 1 END""",
            (profile_id, department)
        )
        await db.commit()
    return RedirectResponse(url="/profile", status_code=303)

@app.post("/profile/{profile_id}/keywords/add")
async def add_keyword(profile_id: int, keyword: str = Form(...)):
    """Agrega una nueva keyword al perfil."""
    keyword = keyword.strip().lower()
    if keyword:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO profile_keywords (profile_id, keyword, is_enabled) VALUES (?,?,1)",
                (profile_id, keyword)
            )
            await db.commit()
    return RedirectResponse(url="/profile", status_code=303)

@app.post("/profile/keywords/{keyword_id}/delete")
async def delete_keyword(keyword_id: int):
    """Elimina una keyword del perfil."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM profile_keywords WHERE id=?", (keyword_id,))
        await db.commit()
    return RedirectResponse(url="/profile", status_code=303)

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
    applied_sites: str = Form("all"),
    is_active: int = Form(1),
):
    """Actualiza un perfil de candidato."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE candidate_profiles SET
                name=?, phone=?, location=?, birth_date=?, civil_status=?, address=?,
                education=?, experience=?, skills=?, languages=?,
                salary_expectation=?, availability=?, about=?, applied_sites=?, is_active=?
            WHERE id=?
        """, (name, phone, location, birth_date, civil_status, address,
              education, experience, skills, languages,
              salary_expectation, availability, about, applied_sites, is_active,
              profile_id))
        await db.commit()
    return RedirectResponse(url="/profile?msg=Perfil+actualizado", status_code=303)

@app.get("/api/stats")
async def get_stats():
    """Retorna estadísticas en JSON."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'applied'") as cursor:
            row = await cursor.fetchone()
            total_applied = row[0] if row else 0
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'new'") as cursor:
            row = await cursor.fetchone()
            total_new = row[0] if row else 0

    return {
        "total_applied": total_applied,
        "total_new": total_new,
        "last_check": datetime.now().isoformat()
    }

# ── Gestión de cookies de sesión ──────────────────────────────────────

@app.post("/api/session")
async def save_session_cookies(request: Request):
    """
    Recibe cookies de sesión desde el navegador local del usuario.
    Las almacena en DB para que el poster las use al postular.
    
    Body JSON esperado: { "site": "tecoloco", "cookies": "COOKIE_HEADER_STRING" }
    """
    body = await request.json()
    site    = body.get("site", "tecoloco")
    cookies = body.get("cookies", "")
    note    = body.get("note", "")

    if not cookies:
        raise HTTPException(status_code=400, detail="cookies vacías")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO session_cookies (site, cookies, updated_at, note)
            VALUES (?, ?, datetime('now', '-6 hours'), ?)
            ON CONFLICT(site) DO UPDATE SET
                cookies    = excluded.cookies,
                updated_at = excluded.updated_at,
                note       = excluded.note
        """, (site, cookies, note))
        await db.commit()

    logger.info(f"[session] Cookies de '{site}' actualizadas ({len(cookies)} chars)")
    return {"ok": True, "site": site, "chars": len(cookies)}

@app.get("/api/session/{site}")
async def get_session_status(site: str):
    """Devuelve el estado de las cookies almacenadas para un sitio."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT site, length(cookies) as len, updated_at, note FROM session_cookies WHERE site=?",
            (site,)
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return {"status": "sin_cookies", "site": site}
    return {"status": "ok", "site": site, "chars": row["len"],
            "updated_at": row["updated_at"], "note": row["note"]}
