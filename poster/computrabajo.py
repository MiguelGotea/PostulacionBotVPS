"""
poster/computrabajo.py — Flujo de postulación en Computrabajo Nicaragua

Estrategia:
  1. Login con Playwright (2 pasos: email → Continuar → password → Iniciar sesión)
  2. Las cookies quedan en el contexto del browser (sin necesidad de inyectar)
  3. Navegar a la URL directa de la oferta
  4. Verificar el departamento (filtro profile_departments, igual que Tecoloco)
  5. Click en botón "Postularme"
  6. Si hay "Preguntas de selección" (Killer Questions):
       → Leer labels + textareas
       → Gemini AI responde
       → Click "Enviar mi CV"
  7. Detectar resultado: éxito, ya postulado, no cumple requisitos
"""
import asyncio
import logging
import random
import re
import unicodedata
import aiosqlite

from poster.base import BasePoster
from poster.ai_responder import answer_questions
from config import PLAYWRIGHT_TIMEOUT, DB_PATH

logger = logging.getLogger(__name__)

BASE_URL  = "https://ni.computrabajo.com"
# Sin Tor: la IP del VPS sí puede acceder a secure.computrabajo.com directamente.
# Tor solo era necesario para las páginas de búsqueda (ni.computrabajo.com/trabajo-de-X).
# El login desde el VPS directo puede ir a la URL base del login OAuth.
LOGIN_ENTRY_URL = "https://secure.computrabajo.com/Account/Login"

# Departamentos de Nicaragua para regex
_DEPTS_REGEX = (
    r"Boaco|Carazo|Chinandega|Chontales|Estelí|Estelí|"
    r"Granada|Jinotega|León|Madriz|Managua|Masaya|Matagalpa|"
    r"Nueva\s+Segovia|Río\s+San\s+Juan|Rivas|RAAN|RAAS"
)


class ComputrabajoPoster(BasePoster):

    def __init__(self):
        super().__init__("computrabajo")
        self._logged_in: bool = False   # caché de sesión para no re-loguear job a job

    # ─────────────────────────────────────────────
    # FILTRO DE DEPARTAMENTO
    # ─────────────────────────────────────────────

    async def _check_department(self, location: str) -> str | None:
        """
        Verifica si la ubicación de la oferta está en los departamentos
        permitidos para el perfil activo.

        Computrabajo usa formato "Ciudad, Departamento" (ej. "Managua, Managua").
        El departamento es la ÚLTIMA parte después de la coma.

        Returns:
            None                           → OK, puede aplicar
            'sin_departamento: ...'        → no especifica departamento concreto
            'departamento_no_permitido: X' → departamento fuera del filtro
        """
        if not location:
            return None  # Sin location → no se filtra

        loc_strip = location.strip()

        # Si solo dice "Nicaragua" sin ciudad/departamento
        if loc_strip.lower() in ("nicaragua", "nicaragua."):
            return "sin_departamento: La oferta no especifica departamento"

        # Extraer departamento: "Managua, Managua" → "Managua" (última parte)
        # También manejar formato sin coma: "Managua"
        parts = [p.strip() for p in loc_strip.split(",")]
        dept_raw = parts[-1] if len(parts) > 1 else parts[0]

        # Leer departamentos habilitados del perfil activo
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    """SELECT pd.department FROM profile_departments pd
                       JOIN candidate_profiles cp ON cp.id = pd.profile_id
                       WHERE cp.is_active = 1 AND pd.is_enabled = 1""",
                ) as cursor:
                    rows     = await cursor.fetchall()
                    allowed  = {r[0].lower() for r in rows}
        except Exception as e:
            logger.debug(f"[{self.site_name}] _check_department error DB: {e}")
            return None  # Si falla la DB, dejar pasar

        if not allowed:
            return None  # Sin configuración → no filtrar

        if dept_raw.lower() in allowed:
            return None  # ✅ Departamento permitido

        return f"departamento_no_permitido: {dept_raw}"

    # ─────────────────────────────────────────────
    # LEER UBICACIÓN DESDE LA PÁGINA (FALLBACK)
    # ─────────────────────────────────────────────

    async def _read_location_from_page(self, page, job_id) -> str:
        """
        Lee la ubicación/departamento desde el panel de detalle de la oferta.
        Se usa cuando la BD no tiene location para este job.
        Guarda el dato en la BD y lo retorna.
        """
        location = ""
        try:
            body_text = await page.inner_text("body")

            # Patrón: "Managua, Managua" | "León, León"
            m = re.search(
                rf"[A-ZÁÉÍÓÚÑa-záéíóúñ][\w\s]+,\s*({_DEPTS_REGEX})",
                body_text,
                re.IGNORECASE,
            )
            if m:
                # Tomar el match completo como "Ciudad, Departamento"
                raw = m.group(0).strip()
                # Limpiar posibles saltos de línea o basura
                raw = re.sub(r"\s+", " ", raw)
                location = raw[:120]

            if location and job_id:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE jobs SET location = ? WHERE id = ? AND (location IS NULL OR location = '')",
                        (location, job_id)
                    )
                    await db.commit()
                logger.info(f"[{self.site_name}] Ubicación leída desde página: '{location}' (id={job_id})")
            elif not location:
                logger.debug(f"[{self.site_name}] No se pudo leer ubicación de: {page.url[:60]}")

        except Exception as e:
            logger.debug(f"[{self.site_name}] _read_location_from_page error: {e}")
        return location

    # ─────────────────────────────────────────────
    # LOGIN (2 pasos con Playwright)
    # ─────────────────────────────────────────────

    async def login(self, page, credentials) -> bool:
        """
        Login de 2 pasos en Computrabajo.
        Flujo: Homepage → click "Login" (nav) → click "Ingresar" → email → Continuar → password → Iniciar
        La URL de login tiene parámetros OAuth dinámicos, no se puede navegar directamente.
        """
        email    = credentials.get("email", "")
        password = credentials.get("password", "")

        if not email or not password:
            logger.error(f"[{self.site_name}] Credenciales vacías")
            return False

        try:
            # ── Paso 0: ir al subdominio de candidatos → redirige al login OAuth ──
            logger.info(f"[{self.site_name}] Login → {LOGIN_ENTRY_URL}")
            await page.goto(LOGIN_ENTRY_URL, wait_until="domcontentloaded", timeout=60000)
            # El subdominio redirige automáticamente a secure.computrabajo.com/Account/Login
            # con los parámetros OAuth correctos — esperar que termine la redirección
            await asyncio.sleep(random.uniform(2, 3))
            logger.info(f"[{self.site_name}] URL tras redirección: {page.url[:100]}")

            # Esperar formulario de login
            try:
                await page.wait_for_selector(
                    "input#Email, input[name='Email'], input[type='email']",
                    timeout=15000
                )
            except Exception:
                # Si redirigió a otro lugar (ya logueado o error), comprobarlo
                url_actual = page.url.lower()
                if "account/login" not in url_actual and "candidato/login" not in url_actual:
                    logger.info(f"[{self.site_name}] Posible sesión activa: {page.url[:80]}")
                    self._logged_in = True
                    return True
                logger.error(f"[{self.site_name}] Página de login no cargó. URL: {page.url[:80]}")
                return False

            await asyncio.sleep(random.uniform(0.5, 1.0))

            # ── Paso 1: email ─────────────────────────────────────────
            email_input = await page.query_selector(
                "input#Email, input[name='Email'], input[type='email']"
            )
            if not email_input:
                logger.error(f"[{self.site_name}] ✗ Campo de email no encontrado. Título: '{await page.title()}'")
                return False
            logger.info(f"[{self.site_name}] ✓ Campo email encontrado. Llenando: {email[:20]}...")
            await email_input.fill(email)
            await asyncio.sleep(random.uniform(0.4, 0.8))

            continue_btn = await page.query_selector(
                "button#continueWithMailButton, "
                "button:has-text('Continuar'), "
                "button[type='submit']"
            )
            if continue_btn:
                logger.info(f"[{self.site_name}] ✓ Botón Continuar encontrado. Haciendo click...")
                await continue_btn.click()
            else:
                logger.warning(f"[{self.site_name}] ✗ Botón Continuar no encontrado. Usando Enter.")
                await email_input.press("Enter")

            # Esperar a que aparezca el campo de contraseña
            try:
                await page.wait_for_selector(
                    "input#password, input[name='password'], input[type='password']",
                    timeout=15000
                )
                logger.info(f"[{self.site_name}] ✓ Campo de contraseña apareció")
            except Exception:
                title = await page.title()
                logger.warning(f"[{self.site_name}] ✗ Campo de password no apareció tras Continuar. Título: '{title}' URL: {page.url[:80]}")
                return False

            await asyncio.sleep(random.uniform(0.5, 1.0))

            # ── Paso 2: contraseña ────────────────────────────────────
            pwd_input = await page.query_selector(
                "input#password, input[name='password'], input[type='password']"
            )
            if not pwd_input:
                logger.error(f"[{self.site_name}] ✗ Campo de contraseña no encontrado tras wait")
                return False
            logger.info(f"[{self.site_name}] ✓ Llenando contraseña...")
            await pwd_input.fill(password)
            await asyncio.sleep(random.uniform(0.4, 0.8))

            submit_btn = await page.query_selector(
                "a#btnSubmitPass, "
                "button:has-text('Iniciar sesión'), "
                "button:has-text('Iniciar'), "
                "button[type='submit']"
            )
            if submit_btn:
                logger.info(f"[{self.site_name}] ✓ Botón submit encontrado. Haciendo click...")
                await submit_btn.click()
            else:
                logger.warning(f"[{self.site_name}] ✗ Botón submit no encontrado. Usando Enter.")
                await pwd_input.press("Enter")

            try:
                await page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            await asyncio.sleep(random.uniform(2, 3))

            # ── Verificar éxito ───────────────────────────────────────
            url_final = page.url.lower()
            title_final = await page.title()
            logger.info(f"[{self.site_name}] URL final login: {page.url[:100]} | Título: '{title_final}'")
            if "account/login" not in url_final:
                logger.info(f"[{self.site_name}] ✅ Login exitoso → {page.url[:60]}")
                self._logged_in = True
                return True
            else:
                logger.error(f"[{self.site_name}] ✗ Login falló — sigue en login. Título: '{title_final}'")
                return False

        except Exception as e:
            logger.error(f"[{self.site_name}] Error en login(): {e}")
            return False

    async def login_once(self, page, credentials=None) -> bool:
        """Loguea solo si no hay sesión activa en caché."""
        if self._logged_in:
            return True
        creds = credentials or {}
        return await self.login(page, creds)

    # ─────────────────────────────────────────────
    # POSTULACIÓN PRINCIPAL
    # ─────────────────────────────────────────────

    async def apply(self, page, job_url: str, credentials=None, job_db_id=None):
        """
        Aplica a una oferta de Computrabajo.

        Flujo:
          1. Login si no hay sesión activa
          2. Verificar departamento (filtro de profile_departments)
          3. Navegar a URL de la oferta
          4. Click "Postularme"
          5. Si hay Killer Questions → Gemini AI responde → "Enviar mi CV"
          6. Verificar resultado: éxito / ya postulado / no cumple

        Returns:
            (True,  None)             → Éxito
            (False, 'no_cumple: X')   → No cumple requisitos
            (False, 'mensaje')        → Fallo técnico
        """
        creds = credentials or {}

        # Si no se pasaron credenciales, obtenerlas de la DB
        if not creds.get("email"):
            profile_id = 1
            if job_db_id:
                try:
                    async with aiosqlite.connect(DB_PATH) as _db:
                        async with _db.execute(
                            "SELECT profile_id FROM jobs WHERE id=?", (job_db_id,)
                        ) as _c:
                            _r = await _c.fetchone()
                            if _r and _r[0]:
                                profile_id = _r[0]
                except Exception:
                    pass
            creds = await self.get_credentials_from_db(profile_id, "computrabajo")

        try:
            logger.info(f"[{self.site_name}] Aplicando a: {job_url}")

            # ── Login (solo una vez por sesión del browser) ─────────
            if not self._logged_in:
                logged = await self.login(page, creds)
                if not logged:
                    return False, "No se pudo iniciar sesión en Computrabajo"

            # ── Filtro de departamento (desde DB) ──────────────────
            location = ""
            if job_db_id:
                try:
                    async with aiosqlite.connect(DB_PATH) as _db:
                        async with _db.execute(
                            "SELECT location FROM jobs WHERE id=?", (job_db_id,)
                        ) as _cur:
                            _row = await _cur.fetchone()
                            if _row:
                                location = _row[0] or ""
                except Exception:
                    pass

            dept_result = await self._check_department(location)
            if dept_result is not None:
                logger.info(f"[{self.site_name}] {dept_result}: '{location}'")
                return False, dept_result

            # ── Navegar a la página de la oferta ───────────────────
            await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(2, 3))

            # Si redirigió al login → sesión inválida
            if "account/login" in page.url.lower():
                self._logged_in = False
                return False, f"Sesión inválida al navegar a la oferta. URL: {page.url}"

            # ── Fallback: leer ubicación desde página ──────────────
            # (si no tenemos location en la BD, la leemos del contenido)
            if not location and job_db_id:
                location = await self._read_location_from_page(page, job_db_id)
                if location:
                    dept_result = await self._check_department(location)
                    if dept_result is not None:
                        logger.info(f"[{self.site_name}] {dept_result}: '{location}' (leído de página)")
                        return False, dept_result

            # ── Detectar "ya postulado" antes de hacer click ───────
            already = await self._is_already_applied(page)
            if already:
                logger.info(f"[{self.site_name}] ✅ Ya postulado anteriormente: {job_url[:60]}")
                return True, None

            # ── Detectar "no cumple requisitos" en la página ───────
            no_cumple = await self._check_no_cumple(page)
            if no_cumple:
                logger.info(f"[{self.site_name}] no_cumple: {no_cumple[:80]}")
                return False, f"no_cumple: {no_cumple}"

            # ── PASO 1: Encontrar y hacer click en "Postularme" ────
            apply_btn = await page.query_selector(
                "button:has-text('Postularme'), "
                "span:has-text('Postularme'), "
                "a:has-text('Postularme'), "
                "button:has-text('Postular'), "
                "span.btn:has-text('Postular'), "
                ".js-post-btn"
            )

            if not apply_btn or not await apply_btn.is_visible():
                # Botón no encontrado → puede ser que no cumple prerequisitos
                motivo = "Botón Postularme no encontrado en la página"
                return False, f"no_cumple: {motivo}"

            logger.info(f"[{self.site_name}] Click en 'Postularme'")
            await apply_btn.click()
            await asyncio.sleep(random.uniform(2, 3))

            # ── PASO 2: Detección del modal / resultado inmediato ──

            # ¿Postulación directa exitosa? (sin preguntas)
            if await self._is_success(page):
                logger.info(f"[{self.site_name}] ✅ Postulación directa exitosa")
                return True, None

            # ¿Ya postulado tras el click?
            if await self._is_already_applied(page):
                logger.info(f"[{self.site_name}] ✅ Ya postulado (detectado post-click)")
                return True, None

            # ── PASO 3: Killer Questions ───────────────────────────
            # Detectar modal de "Preguntas de selección"
            has_questions = await self._handle_killer_questions(page)

            if has_questions is None:
                # handle_killer_questions retorna None si falló algo interno
                return False, "Error al manejar las preguntas de selección"

            if has_questions:
                # Preguntas respondidas y "Enviar mi CV" clickeado
                await asyncio.sleep(random.uniform(2, 3))

            # ── PASO 4: Verificar resultado final ──────────────────
            if await self._is_success(page):
                logger.info(f"[{self.site_name}] ✅ Postulación enviada exitosamente")
                return True, None

            if await self._is_already_applied(page):
                logger.info(f"[{self.site_name}] ✅ Ya postulado (verificación final)")
                return True, None

            # Si aún en la página normal, considerar éxito condicional
            # (algunas ofertas no muestran confirmación explícita)
            if "account/login" not in page.url.lower():
                logger.info(f"[{self.site_name}] ✅ Postulación completada (sin confirmación explícita)")
                return True, None

            return False, f"No se pudo confirmar el resultado. URL: {page.url}"

        except Exception as e:
            logger.error(f"[{self.site_name}] Excepción en apply(): {e}")
            return False, f"Excepción: {str(e)}"

    # ─────────────────────────────────────────────
    # DETECCIÓN DE ESTADOS
    # ─────────────────────────────────────────────

    async def _is_success(self, page) -> bool:
        """Detecta el mensaje de postulación exitosa."""
        try:
            body = await page.inner_text("body")
            frases_ok = (
                "te postulaste correctamente",
                "postulación enviada",
                "hemos recibido tu postulación",
                "tu cv ha sido enviado",
                "inscripción completada",
            )
            body_lower = body.lower()
            return any(f in body_lower for f in frases_ok)
        except Exception:
            return False

    async def _is_already_applied(self, page) -> bool:
        """Detecta si ya se postularon a esta oferta anteriormente."""
        try:
            body = await page.inner_text("body")
            frases_ya = (
                "ya te has postulado",
                "ya postulaste",
                "ya te postulaste",
                "ya has aplicado",
                "already applied",
                "ya estás inscrito",
            )
            body_lower = body.lower()
            if any(f in body_lower for f in frases_ya):
                return True

            # También detectar badge "Postulado" en el botón/panel
            badge = await page.query_selector(
                "span:has-text('Postulado'), "
                ".js-applied, "
                "[class*='applied']:has-text('Postulado')"
            )
            if badge and await badge.is_visible():
                return True
        except Exception:
            pass
        return False

    async def _check_no_cumple(self, page) -> str | None:
        """
        Verifica si hay un aviso de 'No cumples con los requisitos'.
        Retorna el texto del aviso o None si no hay.
        """
        try:
            body = await page.inner_text("body")
            frases_no = (
                "no cumples con los requisitos",
                "no cumple con",
                "requisitos no cumplidos",
            )
            body_lower = body.lower()
            for f in frases_no:
                if f in body_lower:
                    # Intentar extraer el mensaje completo
                    for sel in [".alert-danger", ".alert-warning", ".message-error", ".js-error"]:
                        el = await page.query_selector(sel)
                        if el:
                            return (await el.inner_text()).strip()[:300]
                    return f
        except Exception:
            pass
        return None

    # ─────────────────────────────────────────────
    # KILLER QUESTIONS
    # ─────────────────────────────────────────────

    async def _handle_killer_questions(self, page) -> bool | None:
        """
        Detecta y responde las "Preguntas de selección" (Killer Questions).

        El modal aparece luego del click en "Postularme" con:
          - Título: "Preguntas de selección"
          - Textareas: textarea[id^='KillerQuestions']
          - Labels:    label[for^='KillerQuestions']
          - Botón:     a.b_primary.big o button#sendButton con texto "Enviar mi CV"

        Returns:
            True   → preguntas respondidas y botón final clickeado
            False  → no había preguntas (ya manejado por flujo directo)
            None   → error interno
        """
        try:
            # Detectar si hay modal de preguntas
            await asyncio.sleep(1)  # dar tiempo al modal para renderizar

            # Buscar textareas de Killer Questions
            textareas = await page.query_selector_all(
                "textarea[id^='KillerQuestions'], "
                "textarea[id*='KillerQuestion'], "
                "textarea[name*='KillerQuestion']"
            )

            if not textareas:
                # No hay preguntas → sin acción adicional
                return False

            logger.info(f"[{self.site_name}] {len(textareas)} Killer Question(s) detectadas → Gemini AI")

            # Construir lista de preguntas con ID y texto del label
            questions = []
            for i, textarea in enumerate(textareas):
                ta_id = await textarea.get_attribute("id") or f"kq_{i}"

                # Buscar el label que corresponde a este textarea
                q_text = ""
                label_el = await page.query_selector(f"label[for='{ta_id}']")
                if label_el:
                    q_text = (await label_el.inner_text()).strip()
                else:
                    # Fallback: labels en orden
                    all_labels = await page.query_selector_all(
                        "label[for^='KillerQuestions'], label[for*='KillerQuestion']"
                    )
                    if i < len(all_labels):
                        q_text = (await all_labels[i].inner_text()).strip()

                questions.append({"id": ta_id, "text": q_text})
                logger.debug(f"[{self.site_name}] Q{i+1}: '{q_text[:60]}'")

            # Obtener perfil del candidato y generar respuestas con Gemini
            profile = await self.get_candidate_profile()
            answers = await answer_questions(questions, profile)

            # Rellenar los textareas
            for q in questions:
                ans = answers.get(q["id"], "")
                if not ans:
                    # Respuesta genérica de emergencia
                    ans = "Cuento con la experiencia y disponibilidad requerida. Estoy interesada en la posición."
                el = await page.query_selector(f"#{q['id']}")
                if el:
                    await el.fill(ans[:500])   # Computrabajo tiene máx 500 chars
                    await asyncio.sleep(random.uniform(0.4, 1.0))
                    logger.debug(f"[{self.site_name}] Respondido '{q['id']}': {ans[:60]}...")

            # Click en el botón final "Enviar mi CV"
            send_btn = await page.query_selector(
                "a.b_primary.big, "
                "button#sendButton, "
                "button:has-text('Enviar mi CV'), "
                "a:has-text('Enviar mi CV'), "
                "button:has-text('Enviar'), "
                "button.b_primary"
            )

            if not send_btn:
                logger.warning(f"[{self.site_name}] Botón 'Enviar mi CV' no encontrado")
                return None

            logger.info(f"[{self.site_name}] Click en 'Enviar mi CV'")
            await send_btn.click()
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(random.uniform(2, 3))

            return True

        except Exception as e:
            logger.error(f"[{self.site_name}] Error en _handle_killer_questions: {e}")
            return None
