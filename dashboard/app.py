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
            WHERE status IN ('applied', 'failed') 
            ORDER BY date_applied DESC LIMIT 50
        """) as cursor:
            jobs = await cursor.fetchall()

    return templates.TemplateResponse("applied.html", {"request": request, "jobs": jobs})

@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    """Configuración y estadísticas por sitio."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT site, 
                   COUNT(*) as total, 
                   SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) as applied,
                   SUM(CASE WHEN requires_manual=1 THEN 1 ELSE 0 END) as manual
            FROM jobs GROUP BY site
        """) as cursor:
            stats_by_site = await cursor.fetchall()

        # Obtener configuración de sitios activa
        async with db.execute("SELECT * FROM site_configs") as cursor:
            site_configs = await cursor.fetchall()
            
        # Obtener parámetros de aplicación (Salario, etc.)
        async with db.execute("SELECT * FROM app_settings") as cursor:
            app_settings = await cursor.fetchall()
            app_settings_dict = {row['key']: row['value'] for row in app_settings}

    return templates.TemplateResponse("settings.html", {
        "request": request, 
        "keywords": KEYWORDS,
        "stats": stats_by_site,
        "site_configs": site_configs,
        "app_settings": app_settings_dict
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
    
    return RedirectResponse(url="/settings", status_code=303)

@app.post("/settings/scan-site/{site_name}")
async def scan_site(site_name: str, background_tasks: BackgroundTasks):
    """Dispara un escaneo manual de un portal en segundo plano."""
    background_tasks.add_task(run_single_site_scan, site_name)
    return RedirectResponse(url="/settings?msg=Escaneo+iniciado", status_code=303)

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


@app.post("/retry-failed")
async def retry_failed_jobs(site: str = Form(None)):
    """Resetea los jobs fallidos a 'new' para que sean reintentados en el próximo ciclo."""
    async with aiosqlite.connect(DB_PATH) as db:
        if site:
            await db.execute(
                "UPDATE jobs SET status = 'new', error_message = NULL, date_applied = NULL WHERE status = 'failed' AND site = ?",
                (site,)
            )
        else:
            await db.execute(
                "UPDATE jobs SET status = 'new', error_message = NULL, date_applied = NULL WHERE status = 'failed'"
            )
        count = db.total_changes
        await db.commit()
    logger.info(f"Retry: {count} jobs fallidos reseteados a 'new' (site={site or 'todos'})")
    msg = f"{count}+jobs+reseteados+para+reintento"
    return RedirectResponse(url=f"/applied?msg={msg}", status_code=303)

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
