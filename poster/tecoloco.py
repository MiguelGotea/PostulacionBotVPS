import asyncio
import logging
import random
from poster.base import BasePoster
from config import MIN_DELAY, MAX_DELAY, CREDENTIALS, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class TecolocoPoster(BasePoster):
    def __init__(self):
        super().__init__("tecoloco")
        self.login_url = "https://www.tecoloco.com.ni/login.aspx"

    async def login(self, page, credentials) -> bool:
        """Realiza el login de Tecoloco con los selectores exactos de la captura."""
        try:
            logger.info(f"[{self.site_name}] Intentando login con selectores de captura...")
            await page.goto(self.login_url, wait_until="load", timeout=60000)
            await asyncio.sleep(random.uniform(2, 4))
            
            # Selectores exactos según la imagen de "INICIO DE SESIÓN PARA CANDIDATOS"
            email_selector = "#txtEmail, input[id*='Email']"
            pass_selector = "#txtPassword, input[id*='Password']"
            login_btn_selector = "button:has-text('INICIO CANDIDATOS'), .btn-login, input[type='submit']"

            await page.wait_for_selector(email_selector, timeout=20000)
            
            # Escribir email
            await page.click(email_selector)
            await page.keyboard.type(credentials['email'], delay=random.uniform(50, 150))
            
            # Escribir password
            await page.click(pass_selector)
            await page.keyboard.type(credentials['password'], delay=random.uniform(50, 150))
            
            await asyncio.sleep(1)
            
            # Click en el botón VERDE de "INICIO CANDIDATOS"
            logger.info(f"[{self.site_name}] Haciendo clic en INICIO CANDIDATOS...")
            await page.click(login_btn_selector)
            
            # Esperar a redirección
            await asyncio.sleep(6)
            
            current_url = page.url.lower()
            if "login.aspx" not in current_url:
                logger.info(f"[{self.site_name}] Login exitoso (URL actual: {current_url})")
                return True
            
            # Verificación por nombre de usuario en menú (como se ve en tu captura de sesión exitosa)
            user_label = await page.query_selector(".user-info, #liUser, .user-name")
            if user_label:
                logger.info(f"[{self.site_name}] Login exitoso (Usuario detectado)")
                return True

            logger.error(f"[{self.site_name}] Fallo de login: Se mantiene en la página de acceso.")
            return False
                
        except Exception as e:
            logger.error(f"[{self.site_name}] Error crítico en login: {e}")
            return False

    async def apply(self, page, job_url) -> bool:
        """Postulación simplificada."""
        try:
            logger.info(f"[{self.site_name}] Navegando a la oferta...")
            await page.goto(job_url, wait_until="load", timeout=60000)
            await asyncio.sleep(3)

            # Botón "Postularme" / "Aplicar"
            apply_btn = await page.query_selector("a.btn-postularme, #btnAplicar, .btn-primary")
            if not apply_btn:
                logger.warning(f"[{self.site_name}] Botón de postulación no encontrado.")
                return False
                
            await apply_btn.click()
            await asyncio.sleep(5)
            
            # Si hay un paso intermedio de confirmación
            confirm = await page.query_selector("input[value*='Enviar'], button[id*='Finish']")
            if confirm:
                await confirm.click()
                await asyncio.sleep(2)

            logger.info(f"[{self.site_name}] Postulación completada.")
            return True

        except Exception as e:
            logger.error(f"[{self.site_name}] Error en aplicación: {e}")
            return False
