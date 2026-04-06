import asyncio
import logging
from poster.base import BasePoster
from config import PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class ComputrabajoPoster(BasePoster):
    def __init__(self):
        super().__init__("computrabajo")
        self.login_url = "https://candidato.ni.computrabajo.com/acceso/"

    async def login(self, page, credentials) -> bool:
        """Login de Computrabajo."""
        try:
            logger.info(f"[{self.site_name}] Intentando login...")
            await page.goto(self.login_url, wait_until="networkidle")
            
            # Selectores de Computrabajo login
            await page.fill("input#UserName, #email_login", credentials['email'])
            await page.fill("input#Password, #pass_login", credentials['password'])
            await page.click("button#btnLogin, #btn_login")
            
            await page.wait_for_load_state("networkidle")
            
            # Verificar si el login fue exitoso (URL cambia o aparece nombre usuario)
            if "acceso" not in page.url:
                logger.info(f"[{self.site_name}] Login exitoso en Computrabajo.")
                return True
            else:
                logger.error(f"[{self.site_name}] Fallo de login en Computrabajo.")
                return False
                
        except Exception as e:
            logger.error(f"[{self.site_name}] Error en login Computrabajo: {e}")
            return False

    async def apply(self, page, job_url) -> bool:
        """Postulación en Computrabajo."""
        try:
            logger.info(f"[{self.site_name}] Accediendo a oferta: {job_url}")
            await page.goto(job_url, wait_until="networkidle")
            await self.human_delay()

            # Buscar botón de inscribirme
            apply_btn = await page.query_selector("button#btnInscribirse, #div_postular button")
            
            if not apply_btn:
                logger.warning(f"[{self.site_name}] Botón de postulación no disponible en {job_url}")
                return False
                
            await apply_btn.click()
            await page.wait_for_load_state("networkidle")
            
            # Manejar preguntas adicionales de Computrabajo
            # Si aparece un formulario con texto libre, completarlo con algo genérico
            textarea = await page.query_selector("textarea[name*='pregunta']")
            if textarea:
                await textarea.fill("Cuento con la experiencia requerida y disponibilidad inmediata. Gracias.")
                
            confirm_btn = await page.query_selector("button#btnContinuar, #btn_postular_complemeta")
            if confirm_btn:
                await confirm_btn.click()
                await page.wait_for_load_state("networkidle")

            logger.info(f"[{self.site_name}] Postulación completada en Computrabajo.")
            return True

        except Exception as e:
            logger.error(f"[{self.site_name}] Error postulando en {job_url}: {e}")
            return False
