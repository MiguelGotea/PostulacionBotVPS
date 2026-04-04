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
        """Realiza el login de Tecoloco simulando comportamiento humano."""
        try:
            logger.info(f"[{self.site_name}] Iniciando sesión en modo sigilo...")
            
            # Usar un User Agent de navegador real
            await page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            })

            await page.goto(self.login_url, wait_until="load", timeout=60000)
            await asyncio.sleep(random.uniform(2, 4))
            
            # Esperar al input de email
            email_selector = "input[type='email'], input[name*='Email'], #txtEmail"
            await page.wait_for_selector(email_selector, timeout=20000)
            
            # Escribir email caracter por caracter
            await page.click(email_selector)
            for char in credentials['email']:
                await page.keyboard.send_character(char)
                await asyncio.sleep(random.uniform(0.1, 0.3))
            
            await asyncio.sleep(random.uniform(0.5, 1.5))
            
            # Escribir password caracter por caracter
            pass_selector = "input[type='password'], #txtPassword, input[name*='Password']"
            await page.click(pass_selector)
            for char in credentials['password']:
                await page.keyboard.send_character(char)
                await asyncio.sleep(random.uniform(0.1, 0.3))
                
            await asyncio.sleep(random.uniform(1, 2))
            
            # Click en el botón de ingresar
            login_btn = "button[type='submit'], #btnLogin, input[type='submit'][value*='Ingresar']"
            await page.click(login_btn)
            
            # Esperar a ver si cambia la URL o aparece el perfil
            await page.wait_for_load_state("load")
            await asyncio.sleep(5)
            
            current_url = page.url.lower()
            if "login.aspx" not in current_url:
                logger.info(f"[{self.site_name}] Login exitoso (URL: {current_url})")
                return True
            
            # Verificación secundaria por elementos visibles
            is_logged_in = await page.query_selector("a[href*='logout'], .user-wrapper, .my-account")
            if is_logged_in:
                logger.info(f"[{self.site_name}] Login exitoso (Detectado elemento de sesión)")
                return True

            logger.error(f"[{self.site_name}] Fallo de login: Sigue en la página de acceso.")
            return False
                
        except Exception as e:
            logger.error(f"[{self.site_name}] Error en proceso de login: {e}")
            return False

    async def apply(self, page, job_url) -> bool:
        """Postulación simplificada y robusta."""
        try:
            logger.info(f"[{self.site_name}] Navegando a: {job_url}")
            await page.goto(job_url, wait_until="load", timeout=60000)
            await asyncio.sleep(random.uniform(3, 5))

            # Buscar botón de aplicación
            apply_selectors = ["a.btn-postularme", "button.apply", "#btnAplicar", ".btn-primary"]
            apply_btn = None
            for sel in apply_selectors:
                apply_btn = await page.query_selector(sel)
                if apply_btn: break
            
            if not apply_btn:
                logger.warning(f"[{self.site_name}] No se detectó botón de postulación.")
                return False
                
            await apply_btn.click()
            await asyncio.sleep(5)
            
            # Comprobar si hay un botón final de confirmación
            confirm_selectors = ["input[value*='Enviar']", "button[id*='Finish']", ".modal-footer .btn-primary"]
            for sel in confirm_selectors:
                confirm = await page.query_selector(sel)
                if confirm:
                    await confirm.click()
                    await asyncio.sleep(3)
                    break

            logger.info(f"[{self.site_name}] Postulación finalizada.")
            return True

        except Exception as e:
            logger.error(f"[{self.site_name}] Error en aplicación: {e}")
            return False
