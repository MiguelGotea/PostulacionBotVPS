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
        
        Returns:
            (True, None)       → Éxito
            (False, 'no_cumple: motivo') → CV no cumple requisitos
            (False, 'mensaje') → Fallo con descripción
        """
        try:
            logger.info(f"[{self.site_name}] Aplicando a: {job_url}")

            # ── PASO 1: Ir a la oferta ──
            await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(2, 4))

            # Si la sesión expiró y redirigió a login
            if "login.aspx" in page.url.lower():
                return False, "Sesión expirada durante apply"

            # ── PASO 2: Detectar y click en APLICAR ──
            # Verificar si ya fue aplicada previamente
            ya_aplicado = await page.query_selector(
                "text='Ya has aplicado', .already-applied, [class*='ya-aplico']"
            )
            if ya_aplicado:
                logger.info(f"[{self.site_name}] Oferta ya aplicada anteriormente.")
                return True, None

            apply_btn = await page.query_selector("a.apply-now, a.apply-now.linktowebsite, #btnAplicar")
            if not apply_btn:
                return False, f"Botón APLICAR no encontrado. URL: {page.url}"

            await apply_btn.click()
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2, 3))

            # ── PASO 3: Selección de CV (/Jobs/Aplicar/{id}) ──
            current = page.url.lower()
            if "/jobs/aplicar/" in current or "/aplicar/" in current:

                # Seleccionar primer radio de CV disponible
                cv_radio = await page.query_selector("input[name='CurriculoId']")
                if cv_radio:
                    is_checked = await cv_radio.is_checked()
                    if not is_checked:
                        await cv_radio.click()
                    await asyncio.sleep(0.5)

                # Detectar mensaje "no cumple requisitos"
                # El mensaje aparece en un div rojo/warning DESPUÉS de seleccionar CV
                no_cumple_el = await page.query_selector(
                    ".alert-warning, .alert-danger, div[class*='alert']:has-text('no cumple'), "
                    "div[class*='alert']:has-text('requisitos')"
                )
                if not no_cumple_el:
                    # Segunda detección por texto
                    no_cumple_el = await page.query_selector("text='no cumple'")

                if no_cumple_el:
                    motivo_raw = (await no_cumple_el.inner_text()).strip()
                    # Limpiar y resumir el motivo
                    motivo = re.sub(r'\s+', ' ', motivo_raw)[:300]
                    logger.info(f"[{self.site_name}] No cumple requisitos: {motivo[:80]}...")
                    return False, f"no_cumple: {motivo}"

                # Click en "IR A PREGUNTAS"
                go_btn = await page.query_selector(
                    "button#goToQuestions, button:has-text('IR A PREGUNTAS'), "
                    "a:has-text('IR A PREGUNTAS')"
                )
                if not go_btn:
                    # Puede que sea aplicación directa sin preguntas
                    go_btn = await page.query_selector(
                        "button#applyButton, button.btn-aplicar, button:has-text('APLICAR')"
                    )

                if not go_btn:
                    return False, f"Botón para continuar no encontrado. URL: {page.url}"

                await go_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(2, 3))

            # ── PASO 4: Formulario de preguntas (/application/ApplyQuestions) ──
            if "applyquestions" in page.url.lower() or "ApplyQuestions" in page.url:

                profile = await self.get_candidate_profile()

                # Recopilar pares label → textarea
                labels = await page.query_selector_all("form label, .form-group label")
                textareas = await page.query_selector_all("textarea.validar, textarea.required-value")

                questions = []
                for i, textarea in enumerate(textareas):
                    ta_id = await textarea.get_attribute("id") or f"q_{i}"
                    question_text = ""
                    if i < len(labels):
                        question_text = (await labels[i].inner_text()).strip()
                    questions.append({"id": ta_id, "text": question_text})

                logger.info(f"[{self.site_name}] {len(questions)} preguntas detectadas → consultando Gemini AI")

                if questions:
                    answers = await answer_questions(questions, profile)
                    for q in questions:
                        answer = answers.get(q["id"], "")
                        if answer and q["id"]:
                            el = await page.query_selector(f"#{q['id']}")
                            if el:
                                await el.fill(answer)
                                await asyncio.sleep(random.uniform(0.4, 1.0))

                # Submit final
                submit_btn = await page.query_selector(
                    "button#applyButton, button.btn-aplicar.enviarForm, "
                    "button.apply-now, input[type='submit']"
                )
                if not submit_btn:
                    return False, f"Botón de envío final no encontrado. URL: {page.url}"

                await submit_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

            # ── PASO 5: Verificar resultado ──
            final_url = page.url.lower()

            # Indicadores de error
            if "error" in final_url or "login.aspx" in final_url:
                return False, f"Error tras submit. URL final: {page.url}"

            # Indicadores de éxito: mensaje de confirmación o regreso a la oferta
            success_msg = await page.query_selector(
                "text='Postulación enviada', text='Has aplicado', "
                "text='¡Gracias', .alert-success, [class*='success']"
            )
            if success_msg:
                logger.info(f"[{self.site_name}] ✅ Postulación confirmada por mensaje en página")
                return True, None

            # Si llegamos aquí sin errores detectados, asumimos éxito
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
