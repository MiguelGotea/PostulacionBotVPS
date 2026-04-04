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

    async def login_organic(self, page, credentials) -> bool:
        """Realiza el login cuando la web lo solicita (después de aplicar)."""
        try:
            logger.info(f"[{self.site_name}] Detectada página de login (Navegación Orgánica)...")
            
            # Selector de email con paciencia para Akamai
            email_selector = "#Email, input[type='email'], #txtEmail"
            await page.wait_for_selector(email_selector, timeout=60000, state="visible")
            
            # Escribir con comportamiento humano
            await page.fill(email_selector, credentials['email'])
            await asyncio.sleep(random.uniform(0.5, 1.5))
            await page.fill("#Password, #txtPassword", credentials['password'])
            
            await asyncio.sleep(random.uniform(1, 2))
            
            # Click en login
            await page.click("#loginButton, button:has-text('INICIO CANDIDATOS')")
            
            # Tecoloco redirigirá automáticamente a la oferta si hay ReturnUrl
            await page.wait_for_load_state("load")
            return True
        except Exception as e:
            logger.error(f"[{self.site_name}] Error en login orgánico: {e}")
            return False

    async def apply(self, page, job_url, credentials=None) -> bool:
        """Postulación con flujo Humano: Oferta -> Login (si pide) -> Cuestionario."""
        try:
            settings = await self.get_app_settings()
            logger.info(f"[{self.site_name}] Iniciando flujo humano para: {job_url}")
            
            # Ir a la oferta primero
            await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(2, 4))

            # 1. Verificar si hay que loguearse antes de aplicar o si pide login al aplicar
            apply_btn = await page.query_selector(".btn-primary:has-text('APLICAR'), #btnAplicar")
            if not apply_btn:
                # Comprobar si ya aplicamos
                if await page.query_selector("text='Ya has aplicado'"):
                    logger.info(f"[{self.site_name}] Ya aplicado.")
                    return True
                
                # Si estamos en login.aspx directo
                if "login.aspx" in page.url.lower():
                    await self.login_organic(page, credentials or CREDENTIALS['tecoloco'])
                    await asyncio.sleep(3)
                    # El login exitoso debería habernos devuelto a la oferta
                    if "login.aspx" in page.url.lower(): # Si falló el retorno
                         await page.goto(job_url, wait_until="load")
                    apply_btn = await page.wait_for_selector(".btn-primary:has-text('APLICAR'), #btnAplicar", timeout=20000)
                else:
                    return False

            # 2. Click en Aplicar
            await apply_btn.click()
            await asyncio.sleep(4)

            # 3. Si redirige a Login tras el click
            if "login.aspx" in page.url.lower():
                if not await self.login_organic(page, credentials or CREDENTIALS['tecoloco']):
                    return False
                await asyncio.sleep(3)
                # Volver a buscar el botón si la redirección no fue automática
                apply_btn = await page.query_selector(".btn-primary:has-text('APLICAR'), #btnAplicar")
                if apply_btn: await apply_btn.click()

            # 4. Manejo de Cuestionario
            questions_btn = await page.wait_for_selector("text='IR A PREGUNTAS', .btn-success", timeout=15000).catch(lambda e: None)
            if questions_btn:
                await questions_btn.click()
                await asyncio.sleep(3)

            # Rellenar
            salary_input = await page.query_selector("input[type='number'], textarea[placeholder*='expectativa']")
            if salary_input:
                await salary_input.fill(settings['salary'])

            working_radio = await page.query_selector(f"text='{settings['working']}' >> .. >> input[type='radio']")
            if working_radio: await working_radio.click()

            # Finalizar
            final_btn = await page.query_selector("button:has-text('APLICAR'), .btn-primary:has-text('Finalizar')")
            if final_btn:
                await final_btn.click()
                await asyncio.sleep(3)
                logger.info(f"[{self.site_name}] ¡Éxito en postulación orgánica!")
                return True

            return False

        except Exception as e:
            logger.error(f"[{self.site_name}] Error en flujo humano de {self.site_name}: {e}")
            return False

    async def login(self, page, credentials) -> bool:
        """Mantenemos compatibilidad con el scheduler viejo pero redirigimos al flujo orgánico."""
        return True # El login se hará dentro de apply()
