import abc
import asyncio
import random
import logging
import aiosqlite
from config import DB_PATH, USER_AGENTS, MIN_DELAY, MAX_DELAY, GEMINI_AI_FILTER_ENABLED

logger = logging.getLogger(__name__)


class BaseScraper(abc.ABC):
    def __init__(self, site_name: str, profile_id: int = 1):
        self.site_name  = site_name
        self.db_path    = DB_PATH
        self.profile_id = profile_id

    @abc.abstractmethod
    async def scrape(self, playwright) -> list[dict]:
        """Método principal de scraping. Debe ser implementado por cada scraper."""
        pass

    async def _get_keywords(self) -> list[str]:
        """Carga keywords habilitadas del perfil indicado en la DB."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT keyword FROM profile_keywords WHERE profile_id = ? AND is_enabled = 1 ORDER BY id",
                    (self.profile_id,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    keywords = [r[0] for r in rows]
                    if keywords:
                        logger.info(f"[{self.site_name}] Perfil {self.profile_id}: {len(keywords)} keywords activas")
                        return keywords
        except Exception as e:
            logger.error(f"[{self.site_name}] Error cargando keywords: {e}")
        return []

    async def save_jobs(self, jobs: list[dict]) -> int:
        """
        Guarda los trabajos encontrados, opcionalmente filtrando por relevancia IA.
        Solo inserta URLs que no existan ya en DB para este perfil.
        """
        # ── Filtro IA de relevancia ────────────────────────────────────────
        if GEMINI_AI_FILTER_ENABLED and jobs:
            try:
                from ai_filter import filter_jobs_batch
                jobs, rejected = await filter_jobs_batch(jobs, self.profile_id, enabled=True)
                if rejected:
                    logger.info(
                        f"[{self.site_name}] IA rechazó {len(rejected)} ofertas irrelevantes: "
                        + ", ".join(f"'{j.get('title','?')}'" for j in rejected[:3])
                        + ("..." if len(rejected) > 3 else "")
                    )
            except Exception as e:
                logger.warning(f"[{self.site_name}] Error en filtro IA, guardando todo: {e}")

        # ── Persistir en DB ────────────────────────────────────────────────
        new_jobs_count = 0
        async with aiosqlite.connect(self.db_path) as db:
            for job in jobs:
                if await self.is_new_job(db, job['url'], self.profile_id):
                    try:
                        await db.execute("""
                            INSERT INTO jobs (profile_id, title, company, location, url, site, salary, description, requires_manual, date_found)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '-6 hours'))
                        """, (
                            self.profile_id,
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

        logger.info(f"[{self.site_name}] Perfil {self.profile_id}: {new_jobs_count} nuevos empleos guardados.")
        return new_jobs_count

    async def is_new_job(self, db, url: str, profile_id: int = None) -> bool:
        """Verifica si la URL ya existe en la base de datos para este perfil."""
        pid = profile_id if profile_id is not None else self.profile_id
        async with db.execute(
            "SELECT id FROM jobs WHERE url = ? AND profile_id = ?", (url, pid)
        ) as cursor:
            return await cursor.fetchone() is None

    async def get_browser_context(self, playwright, headless: bool = True, proxy: str = None):
        """
        Retorna un contexto de navegador con stealth completo.
        Usa utils/stealth.py si está disponible, sino fallback al método legado.
        """
        try:
            from utils.stealth import stealth_context
            return await stealth_context(playwright, headless=headless, proxy_url=proxy)
        except ImportError:
            # Fallback al método original
            browser = await playwright.chromium.launch(headless=headless)
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
                INSERT INTO scan_log (profile_id, site, jobs_found, errors, scan_date)
                VALUES (?, ?, ?, ?, datetime('now', '-6 hours'))
            """, (self.profile_id, self.site_name, jobs_found, errors))
            await db.commit()

    async def retry_scrape(self, playwright, max_attempts: int = 3, base_delay: float = 30.0) -> list[dict]:
        """
        Ejecuta self.scrape con reintentos y backoff exponencial.
        Útil para llamarlo desde el scheduler en lugar de scrape() directamente.
        """
        delay = base_delay
        for attempt in range(1, max_attempts + 1):
            try:
                return await self.scrape(playwright)
            except Exception as e:
                if attempt == max_attempts:
                    logger.error(f"[{self.site_name}] Falló tras {max_attempts} intentos: {e}")
                    raise
                logger.warning(
                    f"[{self.site_name}] Intento {attempt}/{max_attempts} falló: {e}. "
                    f"Reintentando en {delay:.0f}s..."
                )
                await asyncio.sleep(delay)
                delay *= 2.0  # backoff exponencial
        return []
