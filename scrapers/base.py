import abc
import asyncio
import random
import logging
import aiosqlite
from config import DB_PATH, USER_AGENTS, MIN_DELAY, MAX_DELAY

logger = logging.getLogger(__name__)

class BaseScraper(abc.ABC):
    def __init__(self, site_name: str):
        self.site_name = site_name
        self.db_path = DB_PATH

    @abc.abstractmethod
    async def scrape(self, playwright) -> list[dict]:
        """Método principal de scraping. Debe ser implementado por cada scraper."""
        pass

    async def save_jobs(self, jobs: list[dict]) -> int:
        """
        Guarda los trabajos encontrados en esta búsqueda.
        Los jobs 'new' ya fueron eliminados globalmente al inicio del ciclo.
        Solo inserta URLs que no existan ya en DB (procesadas previamente).
        """
        new_jobs_count = 0
        async with aiosqlite.connect(self.db_path) as db:
            for job in jobs:
                if await self.is_new_job(db, job['url']):
                    try:
                        await db.execute("""
                            INSERT INTO jobs (title, company, location, url, site, salary, description, requires_manual, date_found)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '-6 hours'))
                        """, (
                            job.get('title'),
                            job.get('company'),
                            job.get('location'),
                            job.get('url'),
                            self.site_name,
                            job.get('salary'),
                            job.get('description'),
                            job.get('requires_manual', False)
                        ))
                        new_jobs_count += 1
                    except Exception as e:
                        logger.error(f"Error guardando job de {self.site_name}: {e}")

            await db.commit()

        logger.info(f"[{self.site_name}] {new_jobs_count} nuevos empleos guardados.")
        return new_jobs_count

    async def is_new_job(self, db, url: str) -> bool:
        """Verifica si la URL ya existe en la base de datos."""
        async with db.execute("SELECT id FROM jobs WHERE url = ?", (url,)) as cursor:
            return await cursor.fetchone() is None

    async def get_browser_context(self, playwright):
        """Retorna un contexto de navegador con user-agent aleatorio y viewport random."""
        browser = await playwright.chromium.launch(headless=True) # El headless real se toma de config en main
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={'width': random.randint(1280, 1920), 'height': random.randint(720, 1080)}
        )
        return browser, context

    async def human_delay(self):
        """Espera aleatoria para simular comportamiento humano."""
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        await asyncio.sleep(delay)

    async def log_scan(self, jobs_found: int, errors: str = None):
        """Registra el resultado del escaneo en scan_log."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO scan_log (site, jobs_found, errors, scan_date)
                VALUES (?, ?, ?, datetime('now', '-6 hours'))
            """, (self.site_name, jobs_found, errors))
            await db.commit()
