"""
poster/tecoloco.py — Flujo de postulación en Tecoloco.com.ni

Estrategia de sesión (ReturnUrl):
  Para cada oferta:
    1. Ir directo a /Jobs/Aplicar/{id}
    2. Si redirige a login.aspx?ReturnUrl=... → hacer login AHORA
       → Tecoloco redirige automáticamente de vuelta a /Jobs/Aplicar/{id}
    3. Selección de CV → detección no_cumple → IR A PREGUNTAS
    4. Formulario de preguntas → Gemini AI → APLICAR

Ventaja vs login_once():
  - No depende de que la sesión persista entre navegaciones
  - El primer job hace el login; los siguientes reutilizan la cookie
  - Si la sesión expira a mitad del ciclo, se re-loguea automáticamente
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

BASE_URL  = "https://www.tecoloco.com.ni"
LOGIN_URL = f"{BASE_URL}/login.aspx"


class TecolocoPoster(BasePoster):

    def __init__(self):
        super().__init__("tecoloco")

    # ─────────────────────────────────────────────
    # LOGIN EMBEBIDO (se activa cuando necesario)
    # ─────────────────────────────────────────────

    async def _do_login(self, page, credentials: dict) -> bool:
        """
        Hace login desde la página actual (que ya debe ser login.aspx).
        El ReturnUrl en la URL hará que Tecoloco redirija automáticamente
        a /Jobs/Aplicar/{id} tras el login exitoso.
        """
        creds = credentials or CREDENTIALS.get('tecoloco', {})
        try:
            logger.info(f"[{self.site_name}] Realizando login desde: {page.url[:80]}")

            # Selectores robustos para el formulario de login de Tecoloco
            email_sel = (
                "input[placeholder*='Correo'], "
                "input[type='email'], "
                "#Email, #txtEmail"
            )
            pass_sel = (
                "input[placeholder*='ontraseña'], "
                "input[type='password'], "
                "#Password, #txtPassword"
            )
            btn_sel = (
                "button:has-text('INICIO CANDIDATOS'), "
                "button:has-text('Iniciar sesión'), "
                "#loginButton, "
                "button[type='submit']"
            )

            await page.wait_for_selector(email_sel, timeout=20000, state="visible")
            await page.fill(email_sel, creds['email'])
            await asyncio.sleep(random.uniform(0.4, 0.9))

            await page.fill(pass_sel, creds['password'])
            await asyncio.sleep(random.uniform(0.8, 1.5))

            await page.click(btn_sel)
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2, 3))

            if "login.aspx" in page.url.lower():
                logger.error(f"[{self.site_name}] ❌ Login falló — aún en login.aspx")
                return False

            logger.info(f"[{self.site_name}] ✅ Login exitoso → {page.url[:80]}")
            return True

        except Exception as e:
            logger.error(f"[{self.site_name}] Error en _do_login: {e}")
            return False

    # ─────────────────────────────────────────────
    # POSTULACIÓN PRINCIPAL
    # ─────────────────────────────────────────────

    async def apply(self, page, job_url, credentials=None):
        """
        Aplica a una oferta. Maneja el login inline si es necesario.

        Flujo:
          1. Extrae job_id de la URL
          2. Navega a /Jobs/Aplicar/{id}
          3. Si redirige a login.aspx → hace login → ReturnUrl lleva de vuelta
          4. Selección de CV
          5. Preguntas → Gemini AI
          6. Submit final

        Returns:
            (True,  None)             → Éxito
            (False, 'no_cumple: X')  → No cumple requisitos
            (False, 'mensaje error') → Fallo técnico
        """
        creds = credentials or CREDENTIALS.get('tecoloco', {})

        try:
            logger.info(f"[{self.site_name}] Aplicando a: {job_url}")

            # ── Extraer job_id ──────────────────────────────────────
            match = re.search(r'/(\d{4,})/', job_url)
            if not match:
                return False, f"No se pudo extraer ID de: {job_url}"
            job_id = match.group(1)

            # ── PASO 0: Ir a la página del trabajo y click APLICAR ──
            # Flujo natural: job_page → click APLICAR → /Jobs/Aplicar/{id}
            await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(2, 3))

            # Buscar el botón APLICAR interno (no el externo .linktowebsite)
            apply_btn = await page.query_selector(
                "a.apply-now:not(.linktowebsite), "
                "a#apply-btn, "
                ".btn-apply:not(.linktowebsite), "
                "a[href*='Jobs/Aplicar'], "
                "a:has-text('Postularme'), "
                "a.apply-now"   # fallback: cualquier apply-now
            )

            if not apply_btn:
                # Intentar construir la URL directamente
                logger.warning(f"[{self.site_name}] Botón APLICAR no encontrado en {job_url}, usando URL directa")
                await page.goto(f"{BASE_URL}/Jobs/Aplicar/{job_id}", wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(random.uniform(1.5, 2.5))
            else:
                href = await apply_btn.get_attribute("href") or ""
                logger.info(f"[{self.site_name}] Botón APLICAR encontrado: {href[:60]}")
                await apply_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(2, 3))

            # ── Login inline si fue redirigido a login.aspx ─────────
            if "login.aspx" in page.url.lower():
                logged_in = await self._do_login(page, creds)
                if not logged_in:
                    return False, "Login fallido durante apply"

                # Después del login, volver a la página del trabajo y click APLICAR
                await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(random.uniform(2, 3))

                apply_btn2 = await page.query_selector(
                    "a.apply-now:not(.linktowebsite), "
                    "a[href*='Jobs/Aplicar'], "
                    "a.apply-now"
                )
                if apply_btn2:
                    await apply_btn2.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=30000)
                    await asyncio.sleep(random.uniform(2, 3))
                else:
                    await page.goto(f"{BASE_URL}/Jobs/Aplicar/{job_id}", wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(random.uniform(1.5, 2.5))

                if "login.aspx" in page.url.lower():
                    return False, "Login falló, redirigido de nuevo a login.aspx"

            # ── PASO 1: Selección de CV ─────────────────────────────
            if "/jobs/aplicar/" in page.url.lower():

                # Seleccionar primer CV disponible
                cv_radio = await page.query_selector("input[name='CurriculoId']")
                if cv_radio:
                    if not await cv_radio.is_checked():
                        await cv_radio.click()
                    await asyncio.sleep(0.5)

                # Detectar "no cumple requisitos"
                for sel in [".alert-warning", ".alert-danger", ".alert"]:
                    alert_el = await page.query_selector(sel)
                    if alert_el:
                        texto = (await alert_el.inner_text()).strip().lower()
                        if "no cumple" in texto or "requisito" in texto:
                            motivo = re.sub(r'\s+', ' ', texto)[:300]
                            logger.info(f"[{self.site_name}] no_cumple: {motivo[:80]}")
                            return False, f"no_cumple: {motivo}"

                # Click en "IR A PREGUNTAS" (o APLICAR directo si no hay preguntas)
                go_btn = await page.query_selector(
                    "button#goToQuestions, "
                    "button:has-text('IR A PREGUNTAS'), "
                    "a:has-text('IR A PREGUNTAS')"
                )
                if not go_btn:
                    go_btn = await page.query_selector(
                        "button:has-text('APLICAR'), "
                        "button#applyButton, "
                        "input[type='submit']"
                    )

                if not go_btn:
                    return False, f"Botón continuar no encontrado en: {page.url}"

                await go_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(2, 3))

            # ── PASO 2: Formulario de preguntas ────────────────────
            if "applyquestions" in page.url.lower():

                profile   = await self.get_candidate_profile()
                labels    = await page.query_selector_all("form label, label")
                textareas = await page.query_selector_all("textarea")

                questions = []
                for i, textarea in enumerate(textareas):
                    ta_id  = await textarea.get_attribute("id") or f"q_{i}"
                    q_text = ""
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

            # ── PASO 3: Verificar resultado ─────────────────────────
            final_url = page.url.lower()
            if "login.aspx" in final_url or "error" in final_url:
                return False, f"Error tras submit. URL: {page.url}"

            logger.info(f"[{self.site_name}] ✅ Postulación enviada → {page.url[:80]}")
            return True, None

        except Exception as e:
            logger.error(f"[{self.site_name}] Excepción en apply(): {e}")
            return False, f"Excepción: {str(e)}"

    # ─────────────────────────────────────────────
    # COMPATIBILIDAD CON BASE CLASS
    # ─────────────────────────────────────────────

    async def login(self, page, credentials) -> bool:
        return await self._do_login(page, credentials)

    async def login_once(self, page, credentials=None) -> bool:
        """Deprecated: mantenido para compatibilidad. El login ahora es inline en apply()."""
        return await self._do_login(page, credentials or CREDENTIALS.get('tecoloco', {}))
