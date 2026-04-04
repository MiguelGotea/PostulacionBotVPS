import aiosqlite
import logging
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime

from config import DB_PATH, KEYWORDS, DASHBOARD_PORT

logger = logging.getLogger(__name__)

app = FastAPI(title="Katty Jobs Dashboard")

# Montar estáticos y plantillas
app.mount("/static", StaticFiles(directory="dashboard/static"), name="static")
templates = Jinja2Templates(directory="dashboard/templates")

# La conexión se manejará directamente en cada ruta para evitar problemas de hilos con aiosqlite

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, site: str = None):
    """Página principal con ofertas nuevas."""
    async with await get_db() as db:
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
            total_new = (await cursor.fetchone())[0]

    return templates.TemplateResponse("index.html", {
        "request": request, 
        "jobs": jobs, 
        "total_new": total_new,
        "current_site": site
    })

@app.get("/applied", response_class=HTMLResponse)
async def applied(request: Request):
    """Historial de postulaciones."""
    async with await get_db() as db:
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
    async with await get_db() as db:
        async with db.execute("""
            SELECT site, 
                   COUNT(*) as total, 
                   SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) as applied,
                   SUM(CASE WHEN requires_manual=1 THEN 1 ELSE 0 END) as manual
            FROM jobs GROUP BY site
        """) as cursor:
            stats_by_site = await cursor.fetchall()

    return templates.TemplateResponse("settings.html", {
        "request": request, 
        "keywords": KEYWORDS,
        "stats": stats_by_site
    })

@app.post("/ignore/{job_id}")
async def ignore_job(job_id: int):
    """Marca una oferta como ignorada."""
    async with await get_db() as db:
        await db.execute("UPDATE jobs SET status = 'ignored' WHERE id = ?", (job_id,))
        await db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/apply/{job_id}")
async def force_apply(job_id: int):
    """Fuerza la postulación de una oferta específica."""
    # En una implementación real, dispararíamos el poster aquí
    # Por ahora simplemente marcamos como pendiente o forzamos status
    async with await get_db() as db:
        await db.execute("UPDATE jobs SET status = 'applying' WHERE id = ?", (job_id,))
        await db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.get("/api/stats")
async def get_stats():
    """Retorna estadísticas en JSON."""
    async with await get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'applied'") as cursor:
            total_applied = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'new'") as cursor:
            total_new = (await cursor.fetchone())[0]
            
    return {
        "total_applied": total_applied,
        "total_new": total_new,
        "last_check": datetime.now().isoformat()
    }
