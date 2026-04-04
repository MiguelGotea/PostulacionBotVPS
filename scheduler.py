import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from playwright.async_api import async_playwright

from config import SCAN_INTERVAL_HOURS, MAX_APPLICATIONS_PER_RUN, CREDENTIALS
from scrapers.tecoloco import TecolocoScraper
from scrapers.computrabajo import ComputrabajoScraper
from scrapers.opcionempleo import OpcionempleoScraper
from scrapers.acciontrabajo import AcciontrabajoScraper
from scrapers.encuentra24 import Encuentra24Scraper
from scrapers.linkedin import LinkedinScraper

from poster.tecoloco import TecolocoPoster
from poster.computrabajo import ComputrabajoPoster
from poster.opcionempleo import OpcionempleoPoster
from poster.acciontrabajo import AcciontrabajoPoster

from notifier import send_summary
import aiosqlite
from config import DB_PATH

logger = logging.getLogger(__name__)

async def run_scan_cycle():
    """Ejecuta un ciclo completo de escaneo y postulación."""
    logger.info(f"--- Iniciando ciclo de escaneo: {datetime.now()} ---")
    
    scrapers = [
        TecolocoScraper(),
        ComputrabajoScraper(),
        OpcionempleoScraper(),
        AcciontrabajoScraper(),
        Encuentra24Scraper(),
        LinkedinScraper()
    ]
    
    all_found_jobs = []
    
    async with async_playwright() as p:
        # 1. Correr todos los scrapers
        for scraper in scrapers:
            try:
                jobs = await scraper.scrape(p)
                new_count = await scraper.save_jobs(jobs)
                all_found_jobs.extend(jobs)
                await scraper.log_scan(len(jobs))
            except Exception as e:
                logger.error(f"Error en scraper {scraper.site_name}: {e}")
                await scraper.log_scan(0, str(e))
                
    # 2. Obtener ofertas nuevas para postular automáticamente
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM jobs 
            WHERE status = 'new' AND requires_manual = 0 
            LIMIT ?
        """, (MAX_APPLICATIONS_PER_RUN,)) as cursor:
            to_apply = await cursor.fetchall()
            
        # Obtener manuales recientes para el resumen
        async with db.execute("""
            SELECT * FROM jobs 
            WHERE requires_manual = 1 AND date_found >= datetime('now', '-1 hour')
        """) as cursor:
            rows = await cursor.fetchall()
            manual_jobs = [dict(row) for row in rows]

    # 3. Procesar postulaciones
    applied_successfully = []
    if to_apply:
        logger.info(f"Procesando {len(to_apply)} postulaciones automáticas...")
        
        posters = {
            'tecoloco': TecolocoPoster(),
            'computrabajo': ComputrabajoPoster(),
            'opcionempleo': OpcionempleoPoster(),
            'acciontrabajo': AcciontrabajoPoster()
        }
        
        async with async_playwright() as p:
            for job in to_apply:
                site = job['site']
                poster = posters.get(site)
                
                if not poster or not CREDENTIALS.get(site, {}).get('email'):
                    logger.warning(f"No hay poster o credenciales para {site}. Saltando.")
                    continue
                    
                browser, context = await poster.get_browser_context(p)
                page = await context.new_page()
                
                try:
                    if await poster.login(page, CREDENTIALS[site]):
                        success = await poster.apply(page, job['url'])
                        await poster.mark_applied(job['id'], success)
                        if success:
                            applied_successfully.append(dict(job))
                    else:
                        await poster.mark_applied(job['id'], False, "Fallo de login")
                except Exception as e:
                    logger.error(f"Error procesando postulación para id={job['id']}: {e}")
                    await poster.mark_applied(job['id'], False, str(e))
                finally:
                    await browser.close()

    # 4. Enviar resumen si hubo actividad
    if applied_successfully or manual_jobs:
        stats = {
            'found': len(all_found_jobs),
            'vps_ip': "localhost" # TODO: Obtener IP real si es necesario
        }
        await send_summary(applied_successfully, manual_jobs, stats)

    logger.info(f"--- Fin de ciclo: {datetime.now()} ---")

def start_scheduler():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_scan_cycle, 'interval', hours=SCAN_INTERVAL_HOURS, next_run_time=datetime.now())
    scheduler.start()
    return scheduler
