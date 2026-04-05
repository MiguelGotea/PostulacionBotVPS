"""
poster/tecoloco.py — Flujo completo de postulación en Tecoloco.com.ni

Flujo:
  1. login_once() — Login único al inicio del ciclo (una sola vez por sesión)
  2. apply(page, job_url) — Por cada oferta:
       a. goto(job_url) → click a.apply-now
       b. /Jobs/Aplicar/{id} → seleccionar CV → detectar no_cumple → click goToQuestions
       c. /application/ApplyQuestions → recopilar preguntas → Gemini AI → submit
       d. Detectar confirmación de éxito
"""
import asyncio
import logging
import random
import re
import aiosqlite

from poster.base import BasePoster
from poster.ai_responder import answer_questions
from config import CREDENTIALS, PLAYWRIGHT_TIMEOUT, DB_PATH

logger = logging.getLogger(__name__)

BASE_URL = "https://www.tecoloco.com.ni"
LOGIN_URL = f"{BASE_URL}/login.aspx"


class TecolocoPoster(BasePoster):

    def __init__(self):
        super().__init__("tecoloco")

    # ─────────────────────────────────────────────
    # LOGIN ÚNICO (al inicio del ciclo)
    # ─────────────────────────────────────────────

    async def login_once(self, page, credentials=None) -> bool:
        """
        Hace login UNA sola vez al comienzo del ciclo de postulaciones.
        Mantener la misma page/context activa durante todas las postulaciones
        evita que se vuelva a pedir login en cada oferta.
        """
        creds = credentials or CREDENTIALS['tecoloco']
        try:
            logger.info(f"[{self.site_name}] Iniciando sesión en {LOGIN_URL}")
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(1.5, 3))

            # Esperar campo de email con paciencia (Akamai puede tardar)
            email_sel = "#Email, input[type='email'], #txtEmail"
            await page.wait_for_selector(email_sel, timeout=60000, state="visible")

            # Tipeo humano
            await page.fill(email_sel, creds['email'])
            await asyncio.sleep(random.uniform(0.5, 1.2))
            await page.fill("#Password, #txtPassword", creds['password'])
            await asyncio.sleep(random.uniform(1, 2))

            # Click en login
            await page.click("#loginButton, button[type='submit']")
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # Verificar éxito: ya no estamos en login.aspx
            if "login.aspx" not in page.url.lower():
                logger.info(f"[{self.site_name}] ✅ Login exitoso. Sesión activa.")
                return True
            else:
                logger.error(f"[{self.site_name}] ❌ Login falló — sigue en: {page.url}")
                return False

        except Exception as e:
            logger.error(f"[{self.site_name}] Error en login_once: {e}")
            return False

    # ─────────────────────────────────────────────
    # POSTULACIÓN (una por oferta, misma sesión)
    # ─────────────────────────────────────────────

    async def apply(self, page, job_url, credentials=None):
        """
        Aplica a una oferta individual. Asume sesión activa (login_once ya ejecutado).
        
        Flujo directo:
          1. Extrae el job_id de la URL (/NNNNN/puesto.aspx → NNNNN)
          2. Navega directamente a /Jobs/Aplicar/{job_id} (salta el click en APLICAR)
          3. Selección de CV + detección no_cumple
          4. IR A PREGUNTAS → formulario → submit
        
        Returns:
            (True, None)              → Éxito
            (False, 'no_cumple: X')   → CV no cumple requisitos
            (False, 'mensaje')        → Fallo técnico
        """
        try:
            logger.info(f"[{self.site_name}] Aplicando a: {job_url}")

            # ── Extraer job_id numérico de la URL ──
            # /1066134/asistente-administrativo.aspx → 1066134
            match = re.search(r'/(\d{4,})/', job_url)
            if not match:
                return False, f"No se pudo extraer ID de la URL: {job_url}"
            job_id = match.group(1)

            # ── Ir directamente a la página de selección de CV ──
            aplicar_url = f"{BASE_URL}/Jobs/Aplicar/{job_id}"
            await page.goto(aplicar_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(2, 3))

            # Si la sesión expiró, redirige a login
            if "login.aspx" in page.url.lower():
                return False, "Sesión expirada al navegar a Jobs/Aplicar"

            # ── PASO 1: Selección de CV (/Jobs/Aplicar/{id}) ──
            if "/jobs/aplicar/" in page.url.lower():

                # Seleccionar el primer CV (puede venir ya seleccionado)
                cv_radio = await page.query_selector("input[name='CurriculoId']")
                if cv_radio:
                    is_checked = await cv_radio.is_checked()
                    if not is_checked:
                        await cv_radio.click()
                    await asyncio.sleep(0.5)

                # Detectar "no cumple requisitos"
                for sel in [".alert-warning", ".alert-danger", ".alert"]:
                    alert_el = await page.query_selector(sel)
                    if alert_el:
                        texto = (await alert_el.inner_text()).strip().lower()
                        if "no cumple" in texto or "requisito" in texto:
                            motivo = re.sub(r'\s+', ' ', texto)[:300]
                            logger.info(f"[{self.site_name}] no_cumple detectado: {motivo[:80]}")
                            return False, f"no_cumple: {motivo}"

                # Click en "IR A PREGUNTAS"
                go_btn = await page.query_selector(
                    "button#goToQuestions, "
                    "button:has-text('IR A PREGUNTAS'), "
                    "a:has-text('IR A PREGUNTAS')"
                )
                # Fallback: puede existir botón APLICAR directo (sin preguntas)
                if not go_btn:
                    go_btn = await page.query_selector(
                        "button:has-text('APLICAR'), button#applyButton, "
                        "input[type='submit']"
                    )

                if not go_btn:
                    return False, f"Botón para continuar no encontrado. URL: {page.url}"

                await go_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(2, 3))

            # ── PASO 2: Formulario de preguntas (/application/ApplyQuestions) ──
            if "applyquestions" in page.url.lower():

                profile = await self.get_candidate_profile()
                labels   = await page.query_selector_all("form label, label")
                textareas = await page.query_selector_all("textarea")

                questions = []
                for i, textarea in enumerate(textareas):
                    ta_id   = await textarea.get_attribute("id") or f"q_{i}"
                    q_text  = ""
                    if i < len(labels):
                        q_text = (await labels[i].inner_text()).strip()
                    questions.append({"id": ta_id, "text": q_text})

                logger.info(f"[{self.site_name}] {len(questions)} preguntas → Gemini AI")

                if questions:
                    answers = await answer_questions(questions, profile)
                    for q in questions:
                        ans = answers.get(q["id"], "")
                        if ans and q["id"]:
                            el = await page.query_selector(f"#{q['id']}")
                            if el:
                                await el.fill(ans)
                                await asyncio.sleep(random.uniform(0.4, 1.0))

                # Submit — el botón en la pantalla real dice "APLICAR"
                submit_btn = await page.query_selector(
                    "button:has-text('APLICAR'), "
                    "button#applyButton, "
                    "button.btn-aplicar, "
                    "input[type='submit']"
                )
                if not submit_btn:
                    return False, f"Botón APLICAR final no encontrado. URL: {page.url}"

                await submit_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

            # ── PASO 3: Verificar resultado ──
            final_url = page.url.lower()
            if "login.aspx" in final_url or "error" in final_url:
                return False, f"Error tras submit. URL final: {page.url}"

            logger.info(f"[{self.site_name}] ✅ Postulación enviada. URL final: {page.url}")
            return True, None

        except Exception as e:
            logger.error(f"[{self.site_name}] Excepción en apply(): {e}")
            return False, f"Excepción: {str(e)}"


    # ─────────────────────────────────────────────
    # COMPATIBILIDAD CON BASE CLASS
    # ─────────────────────────────────────────────

    async def login(self, page, credentials) -> bool:
        """Compatibilidad con BasePoster — el login real es login_once()."""
        return await self.login_once(page, credentials)
