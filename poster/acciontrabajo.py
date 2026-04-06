import asyncio
import logging
from poster.base import BasePoster
from config import PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class AcciontrabajoPoster(BasePoster):
    def __init__(self):
        super().__init__("acciontrabajo")
        self.login_url = "https://ni.acciontrabajo.com/perfil/ingresar" # URL estimada

    async def login(self, page, credentials) -> bool:
        """Login de Acciontrabajo."""
        try:
            logger.info(f"[{self.site_name}] Intentando login...")
            await page.goto(self.login_url, wait_until="networkidle")
            
            # Selectores de Acciontrabajo login
            await page.fill("input[name*='email'], #Email", credentials['email'])
            await page.fill("input[name*='password'], #Password", credentials['password'])
            await page.click("button[type='submit'], #btnLogin")
            
            await page.wait_for_load_state("networkidle")
            
            if "ingresar" not in page.url:
                logger.info(f"[{self.site_name}] Login exitoso en Acciontrabajo.")
                return True
            else:
                logger.error(f"[{self.site_name}] Fallo de login en Acciontrabajo.")
                return False
                
        except Exception as e:
            logger.error(f"[{self.site_name}] Error en login Acciontrabajo: {e}")
            return False

    async def apply(self, page, job_url) -> bool:
        """Postulación en Acciontrabajo."""
        try:
            logger.info(f"[{self.site_name}] Accediendo a oferta: {job_url}")
            await page.goto(job_url, wait_until="networkidle")
            await self.human_delay()

            # Buscar botón de aplicar
            apply_btn = await page.query_selector(".btn-aplicar, #btn_aplicar, a[href*='/postular/']")
            
            if not apply_btn:
                logger.warning(f"[{self.site_name}] Botón de postulación no disponible en {job_url}")
                return False
                
            await apply_btn.click()
            await page.wait_for_load_state("networkidle")
            
            logger.info(f"[{self.site_name}] Postulación completada en Acciontrabajo.")
            return True

        except Exception as e:
            logger.error(f"[{self.site_name}] Error postulando en {job_url}: {e}")
            return False
