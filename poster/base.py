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
        elif error and str(error).startswith('sin_departamento:'):
            status = 'sin_departamento'
        elif error and str(error).startswith('departamento_no_permitido:'):
            status = 'departamento_no_permitido'
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
        """
        Contexto de navegador con anti-detección activa + Tor proxy.
        - Proxy Tor (SOCKS5 9050) para evadir bloqueo Cloudflare desde datacenter
        - Desactiva la bandera AutomationControlled  
        - Sobreescribe navigator.webdriver vía init script
        - Simula perfil real: locale NI, zona horaria Managua
        """
        browser = await playwright.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-service-autorun",
                "--password-store=basic",
            ]
        )
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={'width': random.randint(1280, 1920), 'height': random.randint(720, 1080)},
            locale='es-NI',
            timezone_id='America/Managua',
            extra_http_headers={
                'Accept-Language': 'es-NI,es;q=0.9,en;q=0.8',
            }
        )
        # Eliminar la propiedad webdriver que delata al bot
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            window.chrome = { runtime: {} };
        """)
        return browser, context

    async def get_candidate_profile(self, profile_id: int = None) -> dict:
        """
        Obtiene el perfil de candidato desde la DB.
        Si profile_id es None, obtiene el primero activo.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                if profile_id:
                    async with db.execute(
                        "SELECT * FROM candidate_profiles WHERE id = ?", (profile_id,)
                    ) as cursor:
                        row = await cursor.fetchone()
                else:
                    async with db.execute(
                        "SELECT * FROM candidate_profiles WHERE is_active = 1 ORDER BY id LIMIT 1"
                    ) as cursor:
                        row = await cursor.fetchone()
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Error obteniendo candidate_profile: {e}")
        return {}

    async def get_credentials_from_db(self, profile_id: int, site: str = None) -> dict:
        """
        Obtiene las credenciales (email, password) de un candidato para un portal.
        Busca en la tabla profile_credentials.
        """
        site_name = site or self.site_name
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT email, password FROM profile_credentials WHERE profile_id = ? AND site_name = ?",
                    (profile_id, site_name)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return {"email": row["email"], "password": row["password"]}
        except Exception as e:
            logger.error(f"Error obteniendo credenciales para perfil {profile_id}/{site_name}: {e}")
        return {}
