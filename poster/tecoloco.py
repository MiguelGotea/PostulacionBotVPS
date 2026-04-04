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
        """Realiza el login de Tecoloco con mayor tolerancia a esperas."""
        try:
            # Si ya estamos en una página interna, es probable que la sesión siga viva
            if "login.aspx" not in page.url.lower() and "tecoloco" in page.url:
                is_logged_in = await page.query_selector("a[href*='logout'], .user-wrapper")
                if is_logged_in:
                    logger.info(f"[{self.site_name}] Ya logueado, saltando login.")
                    return True

            logger.info(f"[{self.site_name}] Accediendo a página de login...")
            await page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            })

            await page.goto(self.login_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(3, 6))
            
            # Selector de email con mayor tiempo de espera
            email_selector = "input[type='email'], input[name*='Email'], #txtEmail"
            await page.wait_for_selector(email_selector, timeout=40000, state="visible")
            
            # Simular interacción humana
            await page.mouse.move(random.randint(100, 500), random.randint(100, 500))
            await page.click(email_selector)
            await page.keyboard.type(credentials['email'], delay=random.uniform(70, 180))
            
            await asyncio.sleep(random.uniform(1, 2))
            
            pass_selector = "input[type='password'], #txtPassword, input[name*='Password']"
            await page.click(pass_selector)
            await page.keyboard.type(credentials['password'], delay=random.uniform(70, 180))
                
            await asyncio.sleep(random.uniform(1, 3))
            
            # Click en login usando el texto exacto capturado
            login_btn = "button:has-text('INICIO CANDIDATOS'), #btnLogin, .btn-login"
            await page.click(login_btn)
            
            # Esperar pacientemente a la carga post-login
            await page.wait_for_load_state("networkidle", timeout=40000)
            
            if "login.aspx" not in page.url.lower():
                logger.info(f"[{self.site_name}] Login completado correctamente.")
                return True
            
            # Verificación visual final
            is_logged_in = await page.query_selector("a[href*='logout'], .my-account, #liUser")
            if is_logged_in:
                return True

            logger.error(f"[{self.site_name}] No se pudo confirmar el inicio de sesión.")
            return False
                
        except Exception as e:
            logger.error(f"[{self.site_name}] Error en login: {e}")
            return False

    async def apply(self, page, job_url) -> bool:
        """Postulación completa con manejo de cuestionario."""
        try:
            settings = await self.get_app_settings()
            logger.info(f"[{self.site_name}] Postulando a: {job_url}")
            await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(4, 7))

            # 1. Botón inicial
            apply_selectors = ["a.btn-postularme", "#btnAplicar", ".btn-primary:has-text('APLICAR')"]
            apply_btn = None
            for sel in apply_selectors:
                apply_btn = await page.query_selector(sel)
                if apply_btn: break

            if not apply_btn:
                if await page.query_selector("text='Ya has aplicado'"):
                    logger.info(f"[{self.site_name}] Oferta ya aplicada anteriormente.")
                    return True
                return False
                
            await apply_btn.click()
            await asyncio.sleep(6)
            
            # 2. Manejo de Cuestionario / Preguntas
            # El botón puede ser "IR A PREGUNTAS"
            questions_btn = await page.query_selector("text='IR A PREGUNTAS', button:has-text('PREGUNTAS')")
            if questions_btn:
                logger.info(f"[{self.site_name}] Entrando a sección de preguntas...")
                await questions_btn.click()
                await asyncio.sleep(4)

            # Rellenar campo de salario
            salary_input = await page.query_selector("input[type='number'], input[id*='alario']")
            if salary_input:
                await salary_input.fill(settings['salary'])
                await asyncio.sleep(1)

            # Rellenar estado laboral
            working_option = settings['working']
            working_radio = await page.query_selector(f"text='{working_option}' >> .. >> input[type='radio']")
            if working_radio:
                await working_radio.click()

            await asyncio.sleep(2)

            # 3. Finalizar
            final_btn = await page.query_selector("button:has-text('APLICAR'), .btn-primary:has-text('Finalizar')")
            if final_btn:
                await final_btn.click()
                await asyncio.sleep(4)
                logger.info(f"[{self.site_name}] Postulación enviada con éxito.")
                return True

            return False

        except Exception as e:
            logger.error(f"[{self.site_name}] Error en aplicación: {e}")
            return False
