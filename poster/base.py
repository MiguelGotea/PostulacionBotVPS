import abc
import asyncio
import random
import logging
import aiosqlite
from config import DB_PATH, USER_AGENTS, MIN_DELAY, MAX_DELAY

logger = logging.getLogger(__name__)

class BasePoster(abc.ABC):
    def __init__(self, site_name: str):
        self.site_name = site_name
        self.db_path = DB_PATH

    @abc.abstractmethod
    async def login(self, page, credentials) -> bool:
        """Realiza el login en el sitio. Debe ser implementado."""
        pass

    @abc.abstractmethod
    async def apply(self, page, job_url, credentials=None):
        """
        Realiza la postulación a una oferta específica.
        Debe retornar (success: bool, error_msg: str | None).
        """
        pass

    async def already_applied(self, url: str) -> bool:
        """Verifica en DB si ya se postuló a esta URL."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT status FROM jobs WHERE url = ?", (url,)) as cursor:
                row = await cursor.fetchone()
                return row and row[0] == 'applied'

    async def mark_applied(self, job_id, success: bool, error: str = None):
        """
        Actualiza la DB con el resultado de la postulación.
        
        Lógica de status:
          - success=True  → 'applied'
          - error empieza con 'no_cumple:'  → 'no_cumple'
          - cualquier otro error  → 'failed'
        """
        if success:
            status = 'applied'
        elif error and str(error).startswith('no_cumple:'):
            status = 'no_cumple'
        else:
            status = 'failed'

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE jobs 
                SET status = ?, date_applied = datetime('now', '-6 hours'), error_message = ? 
                WHERE id = ?
            """, (status, error, job_id))
            await db.commit()
            logger.info(f"[{self.site_name}] Marcado como {status} id={job_id}")

    async def human_delay(self):
        """Espera aleatoria para simular comportamiento humano."""
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        await asyncio.sleep(delay)

    async def get_browser_context(self, playwright, headless=True):
        """Retorna un contexto de navegador para postulación con viewport aleatorio."""
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={'width': random.randint(1280, 1920), 'height': random.randint(720, 1080)}
        )
        return browser, context

    async def get_candidate_profile(self) -> dict:
        """Obtiene el perfil activo del candidato desde la DB."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM candidate_profiles WHERE is_active = 1 ORDER BY id LIMIT 1"
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return dict(row)
        except Exception as e:
            logger.error(f"Error obteniendo candidate_profile: {e}")
        return {}
