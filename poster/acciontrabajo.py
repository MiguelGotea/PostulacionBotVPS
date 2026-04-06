import asyncio
import logging
import re
import aiosqlite
from poster.base import BasePoster
from config import PLAYWRIGHT_TIMEOUT, DB_PATH

logger = logging.getLogger(__name__)

class AcciontrabajoPoster(BasePoster):
    def __init__(self):
        super().__init__("acciontrabajo")
        self.base_url = "https://ni.acciontrabajo.com/"

    async def _check_department(self, location: str) -> str | None:
        """
        Verifica si el departamento está en los permitidos.
        Formato esperado: 'Municipio , Departamento'
        """
        if not location:
            return None

        # Extraer el departamento: "Malpaisillo , León" -> "León"
        parts = location.split(",")
        if len(parts) > 1:
            dept_raw = parts[1].strip()
        else:
            dept_raw = parts[0].strip()

        # Limpiar posibles ruidos ("Nicaragua", etc)
        dept_raw = dept_raw.replace("Nicaragua", "").strip()
        
        if not dept_raw:
            return "sin_departamento: No se pudo determinar el departamento"

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    """SELECT pd.department FROM profile_departments pd
                       JOIN candidate_profiles cp ON cp.id = pd.profile_id
                       WHERE cp.is_active = 1 AND pd.is_enabled = 1""",
                ) as cursor:
                    rows = await cursor.fetchall()
                    allowed = {r[0].lower() for r in rows}
        except Exception as e:
            logger.debug(f"[{self.site_name}] _check_department error: {e}")
            return None

        if not allowed:
            return None

        if dept_raw.lower() in allowed:
            return None

        return f"departamento_no_permitido: {dept_raw}"

    async def login(self, page, credentials) -> bool:
        """Login de Acciontrabajo."""
        try:
            logger.info(f"[{self.site_name}] Intentando login...")
            await page.goto(self.base_url, wait_until="domcontentloaded")
            
            # Abrir modal de login
            ucell = await page.query_selector("#ucell")
            if ucell:
                await ucell.click()
                await asyncio.sleep(1)
            
            # Selectores de Acciontrabajo login
            # Si el modal no abrió o queremos ir directo:
            if not await page.query_selector("input[name='ea']"):
                 await page.goto(f"{self.base_url}perfil/ingresar", wait_until="domcontentloaded")

            await page.fill("input[name='ea']", credentials['email'])
            await page.fill("input[name='pa']", credentials['password'])
            
            # El botón dice "Acceder"
            await page.click("button:has-text('Acceder'), .btn-primary")
            
            await page.wait_for_load_state("networkidle")
            
            # Verificar si logueó (el icono de power aparece o desaparece el "Tú!")
            # O simplemente ver si ya no estamos en la página de login
            if "ingresar" not in page.url.lower():
                logger.info(f"[{self.site_name}] Login exitoso.")
                return True
            else:
                # Comprobar si hay mensaje de error
                error_msg = await page.query_selector(".alert-danger, .error, .err")
                if error_msg:
                    txt = await error_msg.inner_text()
                    logger.error(f"[{self.site_name}] Fallo de login: {txt.strip()}")
                return False
                
        except Exception as e:
            logger.error(f"[{self.site_name}] Error en login Acciontrabajo: {e}")
            return False

    async def apply(self, page, job_url, credentials=None, job_db_id=None) -> tuple[bool, str | None]:
        """Postulación en Acciontrabajo."""
        try:
            logger.info(f"[{self.site_name}] Accediendo a oferta: {job_url}")
            
            # 1. Recuperar location de la DB si es posible
            location = ""
            if job_db_id:
                try:
                    async with aiosqlite.connect(DB_PATH) as db:
                        async with db.execute("SELECT location FROM jobs WHERE id=?", (job_db_id,)) as cur:
                            row = await cur.fetchone()
                            if row: location = row[0] or ""
                except Exception: pass

            # 2. Verificar departamento
            dept_res = await self._check_department(location)
            if dept_res:
                logger.info(f"[{self.site_name}] Saltando por filtro: {dept_res}")
                return False, dept_res

            # 3. Ir a la página
            await page.goto(job_url, wait_until="domcontentloaded")
            await self.human_delay()

            # Si la página de búsqueda cargó el job en el panel derecho, 
            # tal vez el job_url ya nos lleva al detalle.
            
            # 4. Verificar si ya se aplicó (el botón cambia o hay mensaje)
            body_text = await page.inner_text("body")
            if "ya ha sido enviada" in body_text.lower() or "ya aplicaste" in body_text.lower():
                logger.info(f"[{self.site_name}] Ya se había aplicado a esta oferta.")
                return True, None

            # 5. Click en el botón de aplicar
            # Selector: #show_vacancy_apply_form_button ("Enviar mi candidatura")
            apply_btn = await page.query_selector("#show_vacancy_apply_form_button, :has-text('Enviar mi candidatura')")
            
            if not apply_btn:
                # Quizás hay que hacer click en el título primero si estamos en la lista?
                # Pero asumimos que job_url es el detalle directo.
                logger.warning(f"[{self.site_name}] Botón 'Enviar mi candidatura' no encontrado.")
                return False, "Botón de postulación no encontrado"
                
            await apply_btn.click()
            await asyncio.sleep(2)
            
            # 6. Verificar éxito
            success_msg = await page.query_selector(":has-text('Su candidatura ha sido enviada')")
            if success_msg or "enviada" in (await page.inner_text("body")).lower():
                logger.info(f"[{self.site_name}] ✅ Postulación completada exitosamente.")
                return True, None
            else:
                return False, "No se detectó el mensaje de éxito tras click"

        except Exception as e:
            logger.error(f"[{self.site_name}] Error postulando en {job_url}: {e}")
            return False, str(e)
