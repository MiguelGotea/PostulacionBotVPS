import asyncio
import logging
import random
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from playwright.async_api import async_playwright

from config import SCAN_INTERVAL_HOURS, MAX_APPLICATIONS_PER_RUN, CREDENTIALS, DB_PATH
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

logger = logging.getLogger(__name__)

# Lock global: impide que dos instancias de Playwright corran al mismo tiempo.
# En un VPS de 1 vCPU, tener dos browsers simultáneos causa timeouts.
_cycle_lock = asyncio.Lock()
_is_running  = False

def get_all_scrapers():
    """Retorna una lista con todos los scrapers disponibles."""
    return [
        TecolocoScraper(),
        ComputrabajoScraper(),
        OpcionempleoScraper(),
        AcciontrabajoScraper(),
        Encuentra24Scraper(),
        LinkedinScraper()
    ]

async def run_single_site_scan(site_name: str):
    """Ejecuta el escaneo para un único sitio específico."""
    global _is_running

    if _cycle_lock.locked():
        logger.warning(f"[{site_name}] Escaneo manual solicitado pero hay un ciclo en curso. Esperando...")

    async with _cycle_lock:          # Espera si el ciclo auto está corriendo
        now_managua = datetime.now() - timedelta(hours=6)
        logger.info(f"--- Escaneo manual iniciado: {site_name} ({now_managua}) ---")

        scrapers = get_all_scrapers()
        target_scraper = next((s for s in scrapers if s.site_name == site_name), None)

        if not target_scraper:
            logger.error(f"Scraper no encontrado para el sitio: {site_name}")
            return

        async with async_playwright() as p:
            try:
                jobs = await target_scraper.scrape(p)
                new_count = await target_scraper.save_jobs(jobs)
                await target_scraper.log_scan(len(jobs))
                logger.info(f"[{site_name}] Escaneo manual finalizado. {new_count} nuevas ofertas.")
            except Exception as e:
                logger.error(f"Error en escaneo manual de {site_name}: {e}")
                await target_scraper.log_scan(0, str(e))

async def run_scan_cycle():
    """Ejecuta un ciclo completo de escaneo y postulación."""
    global _is_running

    if _cycle_lock.locked():
        logger.warning("Ciclo anterior aún en curso, saltando este ciclo.")
        return

    async with _cycle_lock:
        _is_running = True
        try:
            await _do_scan_cycle()
        finally:
            _is_running = False

async def _do_scan_cycle():
    """Lógica interna del ciclo (protegida por _cycle_lock)."""
    now_managua = datetime.now() - timedelta(hours=6)
    logger.info(f"--- Iniciando ciclo de escaneo automático: {now_managua} ---")

    scrapers = get_all_scrapers()
    
    # 1. Consultar sitios habilitados
    active_sites = []
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT site_name FROM site_configs WHERE is_enabled = 1") as cursor:
            rows = await cursor.fetchall()
            active_sites = [row[0] for row in rows]
    
    logger.info(f"Sitios activos en este ciclo: {active_sites}")
    
    all_found_jobs = []
    
    async with async_playwright() as p:
        # 2. Correr solo los scrapers habilitados
        for scraper in scrapers:
            if scraper.site_name not in active_sites:
                logger.info(f"[{scraper.site_name}] Saltando (Deshabilitado por el usuario)")
                continue
                
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

    # 3. Procesar postulaciones agrupadas por site
    applied_successfully = []
    if to_apply:
        logger.info(f"Procesando {len(to_apply)} postulaciones automáticas...")

        # Agrupar jobs por site
        from collections import defaultdict
        jobs_by_site = defaultdict(list)
        for job in to_apply:
            jobs_by_site[job['site']].append(job)

        # ── Tecoloco: un único browser para todo el ciclo ──
        if 'tecoloco' in jobs_by_site:
            tecoloco_poster = TecolocoPoster()
            tecoloco_creds  = CREDENTIALS.get('tecoloco', {})
            async with async_playwright() as p:
                browser, context = await tecoloco_poster.get_browser_context(p)
                page = await context.new_page()
                try:
                    for job in jobs_by_site['tecoloco']:
                        try:
                            # apply() hace login inline si la sesión no está activa
                            result = await tecoloco_poster.apply(page, job['url'], tecoloco_creds)
                            success, error_msg = result if isinstance(result, tuple) else (result, None)
                            await tecoloco_poster.mark_applied(job['id'], success, error_msg)
                            if success:
                                applied_successfully.append(dict(job))
                            await asyncio.sleep(random.uniform(3, 6))
                        except Exception as e:
                            logger.error(f"Error en tecoloco job id={job['id']}: {e}")
                            await tecoloco_poster.mark_applied(job['id'], False, str(e))
                finally:
                    await browser.close()

        # ── Otros portales: un browser por job (flujo previo) ──
        other_posters = {
            'computrabajo': ComputrabajoPoster(),
            'opcionempleo': OpcionempleoPoster(),
            'acciontrabajo': AcciontrabajoPoster()
        }

        for site, poster in other_posters.items():
            if site not in jobs_by_site:
                continue
            if not CREDENTIALS.get(site, {}).get('email'):
                logger.warning(f"Sin credenciales para {site}. Saltando.")
                continue

            async with async_playwright() as p:
                for job in jobs_by_site[site]:
                    browser, context = await poster.get_browser_context(p)
                    page = await context.new_page()
                    try:
                        result = await poster.apply(page, job['url'], CREDENTIALS.get(site))
                        success, error_msg = result if isinstance(result, tuple) else (result, None)
                        await poster.mark_applied(job['id'], success, error_msg)
                        if success:
                            applied_successfully.append(dict(job))
                    except Exception as e:
                        logger.error(f"Error en {site} job id={job['id']}: {e}")
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

    now_managua = datetime.now() - timedelta(hours=6)
    logger.info(f"--- Fin de ciclo automático: {now_managua} ---")

def start_scheduler():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_scan_cycle, 'interval', hours=SCAN_INTERVAL_HOURS, next_run_time=datetime.now())
    scheduler.start()
    return scheduler
