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
        """Obtiene la configuración de la DB."""
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
        """Login optimizado y veloz tras superar Akamai."""
        try:
            # Detección de sesión activa
            if "login.aspx" not in page.url.lower() and "tecoloco" in page.url:
                if await page.query_selector("a[href*='logout'], .user-wrapper"):
                    logger.info(f"[{self.site_name}] Sesión activa detectada.")
                    return True

            logger.info(f"[{self.site_name}] Accediendo a Tecoloco (Esperando Akamai)...")
            await page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            })

            await page.goto(self.login_url, wait_until="domcontentloaded", timeout=60000)
            
            # Esperar al formulario (Paciencia para Akamai)
            email_selector = "#Email, input[type='email'], #txtEmail"
            await page.wait_for_selector(email_selector, timeout=90000, state="visible")
            
            # Escritura rápida
            await page.fill(email_selector, credentials['email'])
            await page.fill("#Password, #txtPassword", credentials['password'])
            
            # Click y espera corta (el usuario dice que carga rápido)
            await page.click("#loginButton, button:has-text('INICIO CANDIDATOS')")
            
            # Esperar a que la URL cambie (éxito)
            try:
                await page.wait_for_url(lambda url: "login.aspx" not in url.lower(), timeout=15000)
                logger.info(f"[{self.site_name}] Login exitoso y rápido.")
                return True
            except:
                if await page.query_selector("a[href*='logout']"):
                    return True
                logger.error(f"[{self.site_name}] Error en login tras espera.")
                return False
                
        except Exception as e:
            logger.error(f"[{self.site_name}] Fallo crítico en login: {e}")
            return False

    async def apply(self, page, job_url) -> bool:
        """Flujo de postulación ágil."""
        try:
            settings = await self.get_app_settings()
            logger.info(f"[{self.site_name}] Abriendo oferta...")
            await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
            
            # 1. Aplicar
            apply_btn = await page.wait_for_selector(".btn-primary:has-text('APLICAR'), #btnAplicar", timeout=20000)
            await apply_btn.click()
            
            # 2. Ir a preguntas (Carga rápido según usuario)
            questions_btn = await page.wait_for_selector("text='IR A PREGUNTAS', .btn-success", timeout=15000)
            await questions_btn.click()
            
            # 3. Rellenar cuestionario (Selectores de mi prueba en vivo)
            # Salario (el primero que encuentre tipo number o texto)
            salary_input = await page.wait_for_selector("input[type='number'], textarea[placeholder*='expectativa']", timeout=10000)
            await salary_input.fill(settings['salary'])

            # Estado laboral (No/Sí)
            working_radio = await page.query_selector(f"text='{settings['working']}' >> .. >> input[type='radio']")
            if working_radio:
                await working_radio.click()

            # 4. Finalizar
            final_btn = await page.query_selector("button:has-text('APLICAR'), .btn-primary:has-text('Finalizar')")
            if final_btn:
                await final_btn.click()
                await asyncio.sleep(2)
                logger.info(f"[{self.site_name}] ¡Postulación completada!")
                return True

            return False

        except Exception as e:
            logger.error(f"[{self.site_name}] Error postulando: {e}")
            return False
