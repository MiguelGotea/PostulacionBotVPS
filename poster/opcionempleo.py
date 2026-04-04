import asyncio
import logging
from poster.base import BasePoster
from config import CREDENTIALS, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class OpcionempleoPoster(BasePoster):
    def __init__(self):
        super().__init__("opcionempleo")
        self.login_url = "https://www.opcionempleo.com.ni/login"

    async def login(self, page, credentials) -> bool:
        """Login de Opcionempleo."""
        try:
            logger.info(f"[{self.site_name}] Intentando login...")
            await page.goto(self.login_url, wait_until="networkidle")
            
            # Selectores de Opcionempleo login
            await page.fill("input[name*='email'], #email", credentials['email'])
            await page.fill("input[name*='password'], #password", credentials['password'])
            await page.click("button[type='submit'], #login_button")
            
            await page.wait_for_load_state("networkidle")
            
            # Verificar si el login fue exitoso 
            if "login" not in page.url:
                logger.info(f"[{self.site_name}] Login exitoso en Opcionempleo.")
                return True
            else:
                logger.error(f"[{self.site_name}] Fallo de login en Opcionempleo.")
                return False
                
        except Exception as e:
            logger.error(f"[{self.site_name}] Error en login Opcionempleo: {e}")
            return False

    async def apply(self, page, job_url) -> bool:
        """Postulación en Opcionempleo."""
        try:
            logger.info(f"[{self.site_name}] Accediendo a oferta: {job_url}")
            await page.goto(job_url, wait_until="networkidle")
            await self.human_delay()

            # Buscar botón de aplicar
            apply_btn = await page.query_selector(".btn-apply, a[href*='/apply/']")
            
            if not apply_btn:
                logger.warning(f"[{self.site_name}] Botón de postulación no disponible en {job_url}")
                return False
                
            await apply_btn.click()
            await page.wait_for_load_state("networkidle")
            
            logger.info(f"[{self.site_name}] Postulación completada en Opcionempleo.")
            return True

        except Exception as e:
            logger.error(f"[{self.site_name}] Error postulando en {job_url}: {e}")
            return False
