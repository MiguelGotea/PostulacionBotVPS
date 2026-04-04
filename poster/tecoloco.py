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
        """Realiza el login de Tecoloco de forma robusta."""
        try:
            logger.info(f"[{self.site_name}] Intentando login...")
            await page.goto(self.login_url, wait_until="networkidle")
            
            # Selectores de Tecoloco login actualizados y robustos
            email_input = await page.wait_for_selector("input[id*='Email'], #txtEmail, input[name*='Email']", timeout=10000)
            if not email_input:
                logger.error(f"[{self.site_name}] Input de email no encontrado.")
                return False
                
            await email_input.fill(credentials['email'])
            await page.fill("input[password], #txtPassword, input[name*='Password']", credentials['password'])
            
            # Click en login
            await page.click("button[id*='Login'], input[id*='Login'], #btnLogin, .btn-login")
            
            # Esperar a redirección o cambio de estado
            await asyncio.sleep(5) # Espera breve para procesamiento
            
            if "login.aspx" not in page.url.lower():
                logger.info(f"[{self.site_name}] Login exitoso confirmada por cambio de URL.")
                return True
            
            # Verificar si aparece el botón de "Cerrar Sesión" o el nombre de usuario
            logout_btn = await page.query_selector("a[href*='logout'], .user-menu, #liUser")
            if logout_btn:
                logger.info(f"[{self.site_name}] Login exitoso confirmado por elementos de sesión.")
                return True
                
            logger.error(f"[{self.site_name}] Fallo de login: Se quedó en la página de login.")
            return False
                
        except Exception as e:
            logger.error(f"[{self.site_name}] Error crítico en login: {e}")
            return False

    async def apply(self, page, job_url) -> bool:
        """Postulación en Tecoloco."""
        try:
            logger.info(f"[{self.site_name}] Accediendo a oferta: {job_url}")
            await page.goto(job_url, wait_until="load")
            await self.human_delay()

            # Buscar botón de aplicar (hay varios selectores posibles en Tecoloco)
            apply_btn = await page.wait_for_selector("a.btn-postularme, button.apply, #btnAplicar, .btn-apply", timeout=10000)
            
            if not apply_btn:
                logger.warning(f"[{self.site_name}] No se encontró el botón de 'Aplicar' en la página.")
                return False
                
            await apply_btn.click()
            await asyncio.sleep(3)
            
            # Tecoloco suele pedir una confirmación o tiene un flujo de pasos
            # Verificamos si hay un botón final de "Enviar Postulación" o similar
            finish_btn = await page.query_selector("input[value*='Enviar'], button[id*='Finish'], .btn-primary")
            if finish_btn:
                await finish_btn.click()
                await asyncio.sleep(3)

            logger.info(f"[{self.site_name}] Proceso de postulación finalizado para {job_url}")
            return True

        except Exception as e:
            logger.error(f"[{self.site_name}] Error en postulación de {job_url}: {e}")
            return False
