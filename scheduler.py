import asyncio
import logging
import random
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from playwright.async_api import async_playwright

from config import SCAN_INTERVAL_HOURS, MAX_APPLICATIONS_PER_RUN, DB_PATH
from scrapers.tecoloco import TecolocoScraper
from scrapers.computrabajo import ComputrabajoScraper
from scrapers.opcionempleo import OpcionempleoScraper
from scrapers.acciontrabajo import AcciontrabajoScraper
from scrapers.encuentra24 import Encuentra24Scraper
from scrapers.linkedin import LinkedinScraper
# Nuevos portales (stubs — postulación pendiente de entrenamiento)
from scrapers.magneto import MagnetoScraper
from scrapers.bumeran import BumeranScraper
from scrapers.olx import OLXScraper

from poster.tecoloco import TecolocoPoster
from poster.computrabajo import ComputrabajoPoster
from poster.opcionempleo import OpcionempleoPoster
from poster.acciontrabajo import AcciontrabajoPoster

from notifier import send_summary
import aiosqlite

logger = logging.getLogger(__name__)

# Lock global: impide que dos instancias de Playwright corran al mismo tiempo.
_cycle_lock = asyncio.Lock()
_is_running  = False

SCRAPER_CLASSES = {
    # Portales operativos
    'tecoloco':     TecolocoScraper,
    'computrabajo': ComputrabajoScraper,
    'opcionempleo': OpcionempleoScraper,
    'acciontrabajo': AcciontrabajoScraper,
    'encuentra24':  Encuentra24Scraper,
    'linkedin':     LinkedinScraper,
    # Nuevos portales (stubs — solo escaneo, postulación pendiente de entrenamiento)
    'magneto':  MagnetoScraper,
    'bumeran':  BumeranScraper,
    'olx':      OLXScraper,
}

async def get_active_profiles() -> list[dict]:
    """Retorna todos los perfiles activos de la DB."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name, notification_email FROM candidate_profiles WHERE is_active = 1 ORDER BY id"
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

async def get_profile_credentials(profile_id: int, site: str) -> dict:
    """Obtiene email/password de un perfil para un portal."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT email, password FROM profile_credentials WHERE profile_id=? AND site_name=?",
            (profile_id, site)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"email": row["email"], "password": row["password"]}
    return {}

async def run_single_site_scan(site_name: str, profile_id: int = None):
    """Ejecuta el escaneo manual para un sitio específico y candidato."""
    global _is_running

    if _cycle_lock.locked():
        logger.warning(f"[{site_name}] Escaneo manual solicitado pero hay un ciclo en curso. Esperando...")

    async with _cycle_lock:
        now_managua = datetime.now() - timedelta(hours=6)

        # Si no se especifica perfil, tomar el primer activo
        if profile_id is None:
            profiles = await get_active_profiles()
            profiles_to_scan = profiles
        else:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT id, name, notification_email FROM candidate_profiles WHERE id=?", (profile_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    profiles_to_scan = [dict(row)] if row else []

        if site_name is None:
            # Escaneo global: todos los sitios, todos los perfiles activos
            logger.info(f"--- Escaneo manual GLOBAL iniciado: {now_managua} ---")
            for profile in profiles_to_scan:
                await _scan_profile_sites(profile, site_filter=None)
            return

        logger.info(f"--- Escaneo manual: {site_name} (perfiles: {[p['name'] for p in profiles_to_scan]}) ({now_managua}) ---")

        scraper_cls = SCRAPER_CLASSES.get(site_name)
        if not scraper_cls:
            logger.error(f"Scraper no encontrado para el sitio: {site_name}")
            return

        for profile in profiles_to_scan:
            scraper = scraper_cls(profile_id=profile['id'])
            async with async_playwright() as p:
                try:
                    jobs = await scraper.scrape(p)
                    new_count = await scraper.save_jobs(jobs)
                    await scraper.log_scan(len(jobs))
                    logger.info(f"[{site_name}] Perfil '{profile['name']}': {new_count} nuevas ofertas.")
                except Exception as e:
                    logger.error(f"Error en escaneo manual de {site_name} / {profile['name']}: {e}")
                    await scraper.log_scan(0, str(e))

async def _scan_profile_sites(profile: dict, site_filter: str = None):
    """Escanea los sitios activos para un candidato específico."""
    profile_id = profile['id']
    profile_name = profile['name']

    # Consultar sitios habilitados globalmente
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT site_name FROM site_configs WHERE is_enabled = 1") as cursor:
            rows = await cursor.fetchall()
            active_sites = [row[0] for row in rows]

    if site_filter:
        active_sites = [s for s in active_sites if s == site_filter]

    logger.info(f"[Perfil: {profile_name}] Sitios activos: {active_sites}")

    async with async_playwright() as p:
        for site_name, scraper_cls in SCRAPER_CLASSES.items():
            if site_name not in active_sites:
                continue
            scraper = scraper_cls(profile_id=profile_id)
            try:
                jobs = await scraper.scrape(p)
                new_count = await scraper.save_jobs(jobs)
                await scraper.log_scan(len(jobs))
                logger.info(f"[{site_name}][{profile_name}] {new_count} nuevas ofertas.")
            except Exception as e:
                logger.error(f"Error scraper {site_name} / {profile_name}: {e}")
                await scraper.log_scan(0, str(e))

async def run_scan_cycle():
    """Ejecuta un ciclo completo de escaneo y postulación para TODOS los candidatos activos."""
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
    """Lógica interna del ciclo multi-perfil."""
    now_managua = datetime.now() - timedelta(hours=6)
    logger.info(f"--- Iniciando ciclo automático: {now_managua} ---")

    profiles = await get_active_profiles()
    if not profiles:
        logger.warning("Sin perfiles activos. Abortando ciclo.")
        return

    # Limpiar jobs pendientes 'new' de todos los perfiles
    async with aiosqlite.connect(DB_PATH) as db:
        result = await db.execute("DELETE FROM jobs WHERE status = 'new'")
        if result.rowcount:
            logger.info(f"Inicio de ciclo: {result.rowcount} jobs pendientes eliminados.")
        await db.commit()

    all_applied_by_profile = {}
    all_manual_by_profile = {}

    for profile in profiles:
        profile_id   = profile['id']
        profile_name = profile['name']
        logger.info(f"\n=== Procesando candidato: {profile_name} (id={profile_id}) ===")

        # 1. Escanear sitios para este perfil
        await _scan_profile_sites(profile)

        # 2. Obtener ofertas nuevas para postular
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM jobs 
                WHERE status = 'new' AND requires_manual = 0 AND profile_id = ?
                LIMIT ?
            """, (profile_id, MAX_APPLICATIONS_PER_RUN)) as cursor:
                to_apply = await cursor.fetchall()

            async with db.execute("""
                SELECT * FROM jobs 
                WHERE requires_manual = 1 AND profile_id = ? 
                  AND date_found >= datetime('now', '-1 hour')
            """, (profile_id,)) as cursor:
                rows = await cursor.fetchall()
                manual_jobs = [dict(row) for row in rows]

        # 3. Postulaciones por sitio
        applied_successfully = []
        if to_apply:
            logger.info(f"[{profile_name}] {len(to_apply)} postulaciones automáticas...")

            from collections import defaultdict
            jobs_by_site = defaultdict(list)
            for job in to_apply:
                jobs_by_site[job['site']].append(job)

            # Tecoloco
            if 'tecoloco' in jobs_by_site:
                creds = await get_profile_credentials(profile_id, 'tecoloco')
                tecoloco_poster = TecolocoPoster()
                async with async_playwright() as p:
                    browser, context = await tecoloco_poster.get_browser_context(p)
                    page = await context.new_page()
                    try:
                        for job in jobs_by_site['tecoloco']:
                            try:
                                result = await tecoloco_poster.apply(page, job['url'], creds, job_db_id=job['id'])
                                success, error_msg = result if isinstance(result, tuple) else (result, None)
                                await tecoloco_poster.mark_applied(job['id'], success, error_msg)
                                if success:
                                    applied_successfully.append(dict(job))
                                await asyncio.sleep(random.uniform(3, 6))
                            except Exception as e:
                                logger.error(f"Error tecoloco [{profile_name}] id={job['id']}: {e}")
                                await tecoloco_poster.mark_applied(job['id'], False, str(e))
                    finally:
                        await browser.close()

            # Computrabajo (browser persistente con sesión, igual que Tecoloco)
            if 'computrabajo' in jobs_by_site:
                creds = await get_profile_credentials(profile_id, 'computrabajo')
                ct_poster = ComputrabajoPoster()
                async with async_playwright() as p:
                    browser, context = await ct_poster.get_browser_context(p)
                    page = await context.new_page()
                    try:
                        for job in jobs_by_site['computrabajo']:
                            try:
                                result = await ct_poster.apply(page, job['url'], creds, job_db_id=job['id'])
                                success, error_msg = result if isinstance(result, tuple) else (result, None)
                                await ct_poster.mark_applied(job['id'], success, error_msg)
                                if success:
                                    applied_successfully.append(dict(job))
                                await asyncio.sleep(random.uniform(3, 6))
                            except Exception as e:
                                logger.error(f"Error computrabajo [{profile_name}] id={job['id']}: {e}")
                                await ct_poster.mark_applied(job['id'], False, str(e))
                    finally:
                        await browser.close()

            # Otros portales (opcionempleo, acciontrabajo)
            other_posters = {
                'opcionempleo': OpcionempleoPoster(),
                'acciontrabajo': AcciontrabajoPoster()
            }
            for site, poster in other_posters.items():
                if site not in jobs_by_site:
                    continue
                creds = await get_profile_credentials(profile_id, site)
                if not creds.get('email'):
                    logger.warning(f"Sin credenciales para {site} / {profile_name}. Saltando.")
                    continue
                async with async_playwright() as p:
                    for job in jobs_by_site[site]:
                        browser, context = await poster.get_browser_context(p)
                        page = await context.new_page()
                        try:
                            result = await poster.apply(page, job['url'], creds)
                            success, error_msg = result if isinstance(result, tuple) else (result, None)
                            await poster.mark_applied(job['id'], success, error_msg)
                            if success:
                                applied_successfully.append(dict(job))
                        except Exception as e:
                            logger.error(f"Error {site} [{profile_name}] id={job['id']}: {e}")
                            await poster.mark_applied(job['id'], False, str(e))
                        finally:
                            await browser.close()

        all_applied_by_profile[profile_id] = applied_successfully
        all_manual_by_profile[profile_id] = manual_jobs

    # 4. Notificaciones por candidato
    for profile in profiles:
        pid    = profile['id']
        recipient = profile.get('notification_email', '')
        applied = all_applied_by_profile.get(pid, [])
        manual  = all_manual_by_profile.get(pid, [])
        if (applied or manual) and recipient:
            stats = {'found': len(applied) + len(manual), 'vps_ip': 'localhost'}
            await send_summary(applied, manual, stats, recipient_email=recipient, candidate_name=profile['name'])

    now_managua = datetime.now() - timedelta(hours=6)
    logger.info(f"--- Fin de ciclo automático: {now_managua} ---")

def start_scheduler():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_scan_cycle, 'interval', hours=SCAN_INTERVAL_HOURS, next_run_time=datetime.now())
    scheduler.start()
    return scheduler
