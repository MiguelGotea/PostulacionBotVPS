import asyncio
import logging
import random
import aiosqlite
from poster.base import BasePoster
from config import MIN_DELAY, MAX_DELAY, CREDENTIALS, PLAYWRIGHT_TIMEOUT, DB_PATH

logger = logging.getLogger(__name__)

class TecolocoPoster(BasePoster):
    def __init__(self):
        super().__init__("tecoloco")
        self.login_url = "https://www.tecoloco.com.ni/login.aspx"

    async def get_app_settings(self):
        """Obtiene la configuración de salario y estado laboral de la DB."""
        settings = {'salary': '12000', 'working': 'No'}
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM app_settings") as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        if row['key'] == 'tecoloco_salary': settings['salary'] = row['value']
                        if row['key'] == 'tecoloco_working': settings['working'] = row['value']
        except Exception as e:
            logger.error(f"Error cargando app_settings: {e}")
        return settings

    async def login(self, page, credentials) -> bool:
        """Realiza el login de Tecoloco simulando comportamiento humano."""
        try:
            logger.info(f"[{self.site_name}] Iniciando sesión en modo sigilo...")
            await page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            })

            await page.goto(self.login_url, wait_until="load", timeout=60000)
            await asyncio.sleep(random.uniform(2, 4))
            
            email_selector = "input[type='email'], input[name*='Email'], #txtEmail"
            await page.wait_for_selector(email_selector, timeout=20000)
            
            await page.click(email_selector)
            await page.keyboard.type(credentials['email'], delay=random.uniform(50, 150))
            
            pass_selector = "input[type='password'], #txtPassword, input[name*='Password']"
            await page.click(pass_selector)
            await page.keyboard.type(credentials['password'], delay=random.uniform(50, 150))
                
            await asyncio.sleep(random.uniform(1, 2))
            
            login_btn = "button:has-text('INICIO CANDIDATOS'), #btnLogin, .btn-login"
            await page.click(login_btn)
            
            await page.wait_for_load_state("load")
            await asyncio.sleep(5)
            
            if "login.aspx" not in page.url.lower():
                logger.info(f"[{self.site_name}] Login exitoso.")
                return True
            
            is_logged_in = await page.query_selector("a[href*='logout'], .user-wrapper, .my-account")
            if is_logged_in:
                logger.info(f"[{self.site_name}] Login exitoso (Detectado elemento de sesión)")
                return True

            logger.error(f"[{self.site_name}] Fallo de login.")
            return False
                
        except Exception as e:
            logger.error(f"[{self.site_name}] Error en login: {e}")
            return False

    async def apply(self, page, job_url) -> bool:
        """Postulación completa incluyendo cuestionario de preguntas."""
        try:
            settings = await self.get_app_settings()
            logger.info(f"[{self.site_name}] Navegando a: {job_url}")
            await page.goto(job_url, wait_until="load", timeout=60000)
            await asyncio.sleep(random.uniform(3, 5))

            # 1. Click en Aplicar inicial
            apply_btn = await page.query_selector("a.btn-postularme, button.apply, #btnAplicar, .btn-primary")
            if not apply_btn:
                logger.warning(f"[{self.site_name}] No se detectó botón de postulación inicial.")
                # Verificar si ya postulamos
                if await page.query_selector("text='Ya has aplicado'"):
                    logger.info(f"[{self.site_name}] Ya se ha aplicado a esta oferta.")
                    return True
                return False
                
            await apply_btn.click()
            await asyncio.sleep(5)
            
            # 2. Paso "IR A PREGUNTAS"
            questions_btn = await page.query_selector("text='IR A PREGUNTAS', button:has-text('PREGUNTAS'), .btn-success")
            if questions_btn:
                logger.info(f"[{self.site_name}] Entrando al cuestionario...")
                await questions_btn.click()
                await asyncio.sleep(3)

            # 3. Rellenar Cuestionario
            # Expectativa salarial
            salary_input = await page.query_selector("input[type='number'], input[name*='salario'], input[id*='salario']")
            if salary_input:
                logger.info(f"[{self.site_name}] Ingresando salario: {settings['salary']}")
                await salary_input.fill(settings['salary'])

            # Se encuentra laborando
            if settings['working'] == 'No':
                working_radio = await page.query_selector("text='No' >> .. >> input[type='radio']")
                if working_radio:
                    await working_radio.click()
            else:
                working_radio = await page.query_selector("text='Sí' >> .. >> input[type='radio']")
                if working_radio:
                    await working_radio.click()

            await asyncio.sleep(2)

            # 4. Botón Aplicar Final
            final_btn = await page.query_selector("button:has-text('APLICAR'), input[value*='Aplicar'], .btn-primary")
            if final_btn:
                logger.info(f"[{self.site_name}] Haciendo clic en Aplicar final...")
                await final_btn.click()
                await asyncio.sleep(5)
            else:
                logger.warning(f"[{self.site_name}] No se encontró el botón de Aplicar final.")
                return False

            logger.info(f"[{self.site_name}] Postulación completa exitosa.")
            return True

        except Exception as e:
            logger.error(f"[{self.site_name}] Error en proceso de aplicación: {e}")
            return False
