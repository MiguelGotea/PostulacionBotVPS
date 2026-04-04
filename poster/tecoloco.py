import asyncio
import logging
from poster.base import BasePoster
from config import MIN_DELAY, MAX_DELAY, CREDENTIALS, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class TecolocoPoster(BasePoster):
    def __init__(self):
        super().__init__("tecoloco")
        self.login_url = "https://www.tecoloco.com.ni/login.aspx"

    async def login(self, page, credentials) -> bool:
        """Realiza el login de Tecoloco."""
        try:
            logger.info(f"[{self.site_name}] Intentando login...")
            await page.goto(self.login_url, wait_until="networkidle")
            
            # Selectores de Tecoloco login (ejemplos comunes)
            await page.fill("input[name*='Email'], #txtEmail", credentials['email'])
            await page.fill("input[name*='Password'], #txtPassword", credentials['password'])
            await page.click("button[type='submit'], input[type='submit'], #btnLogin")
            
            await page.wait_for_load_state("networkidle")
            
            # Verificar si el login fue exitoso (URL cambia o aparece nombre usuario)
            if "login.aspx" not in page.url:
                logger.info(f"[{self.site_name}] Login exitoso.")
                return True
            else:
                logger.error(f"[{self.site_name}] Fallo de login: URL no cambió.")
                return False
                
        except Exception as e:
            logger.error(f"[{self.site_name}] Error en login: {e}")
            return False

    async def apply(self, page, job_url) -> bool:
        """Postulación en Tecoloco."""
        try:
            logger.info(f"[{self.site_name}] Accediendo a oferta: {job_url}")
            await page.goto(job_url, wait_until="networkidle")
            await self.human_delay()

            # Buscar botón de postularme
            apply_btn = await page.query_selector("a.btn-postularme, button.apply, #btnAplicar")
            
            if not apply_btn:
                logger.warning(f"[{self.site_name}] Botón de postulación no encontrado en {job_url}")
                return False
                
            await apply_btn.click()
            await page.wait_for_load_state("networkidle")
            
            # Manejar posibles modales de confirmación
            confirm_btn = await page.query_selector("button.confirm-apply, .modal-footer button.primary")
            if confirm_btn:
                await confirm_btn.click()
                await page.wait_for_load_state("networkidle")

            logger.info(f"[{self.site_name}] Postulación completada para {job_url}")
            return True

        except Exception as e:
            logger.error(f"[{self.site_name}] Error postulando en {job_url}: {e}")
            return False
