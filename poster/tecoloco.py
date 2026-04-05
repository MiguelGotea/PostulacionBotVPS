"""
poster/tecoloco.py — Flujo de postulación en Tecoloco.com.ni

Estrategia de autenticación:
  1. _login_via_http() → aiohttp hace POST del formulario ASP.NET
     (sin browser → evita detección de Akamai/bot challenges en login)
  2. Las cookies de sesión se inyectan en el contexto de Playwright
  3. Playwright navega a la página del job y hace click en APLICAR
     (ya tiene sesión válida → va directo a /Jobs/Aplicar/{id})
  4. Selección de CV → preguntas → Gemini AI → APLICAR
"""
import asyncio
import logging
import random
import re
import aiohttp

from poster.base import BasePoster
from poster.ai_responder import answer_questions
from config import CREDENTIALS, PLAYWRIGHT_TIMEOUT, DB_PATH

logger = logging.getLogger(__name__)

BASE_URL  = "https://www.tecoloco.com.ni"
LOGIN_URL = f"{BASE_URL}/login.aspx"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-NI,es;q=0.9,en;q=0.8",
}


class TecolocoPoster(BasePoster):

    def __init__(self):
        super().__init__("tecoloco")
        self._session_cookies: list[dict] = []   # cache de cookies entre jobs

    # ─────────────────────────────────────────────
    # LOGIN VÍA HTTP  (sin browser headless)
    # ─────────────────────────────────────────────

    async def _login_via_http(self, credentials: dict) -> list[dict] | None:
        """
        Hace login mediante HTTP POST directo (aiohttp).
        Evita la detección del navegador headless en la página de login.

        Retorna la lista de cookies para inyectar en el contexto de Playwright.
        """
        email    = credentials.get('email', '')
        password = credentials.get('password', '')

        try:
            jar = aiohttp.CookieJar(unsafe=True)
            async with aiohttp.ClientSession(cookie_jar=jar, headers=_HEADERS) as session:

                # 1. GET login page → extraer campos ocultos ASP.NET ──────────
                async with session.get(LOGIN_URL) as resp:
                    html = await resp.text()

                vs_match  = re.search(r'id="__VIEWSTATE"\s+value="([^"]*)"', html)
                evv_match = re.search(r'id="__EVENTVALIDATION"\s+value="([^"]*)"', html)
                vsg_match = re.search(r'id="__VIEWSTATEGENERATOR"\s+value="([^"]*)"', html)

                if not vs_match:
                    # Si no hay VIEWSTATE → página de challenge / bloqueo
                    logger.error(f"[{self.site_name}] HTTP login: no se encontró __VIEWSTATE")
                    logger.debug(f"HTML snippet: {html[:600]}")
                    return None

                form_data = {
                    "Email":                   email,
                    "Password":                password,
                    "loginButton":             "INICIO CANDIDATOS",
                    "__VIEWSTATE":             vs_match.group(1),
                    "__EVENTVALIDATION":       evv_match.group(1) if evv_match else "",
                    "__VIEWSTATEGENERATOR":    vsg_match.group(1) if vsg_match else "",
                }

                # 2. POST login form ──────────────────────────────────────────
                post_headers = {
                    **_HEADERS,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": LOGIN_URL,
                }
                async with session.post(
                    LOGIN_URL, data=form_data,
                    headers=post_headers,
                    allow_redirects=True
                ) as resp:
                    final_url = str(resp.url)

                if "login.aspx" in final_url.lower():
                    logger.error(f"[{self.site_name}] HTTP login falló (sigue en login). URL: {final_url}")
                    return None

                # 3. Extraer cookies de la sesión ────────────────────────────
                cookies = []
                for cookie in jar:
                    cookies.append({
                        "name":   cookie.key,
                        "value":  cookie.value,
                        "domain": "www.tecoloco.com.ni",
                        "path":   "/",
                    })

                logger.info(f"[{self.site_name}] ✅ HTTP Login exitoso → {final_url[:60]}  ({len(cookies)} cookies)")
                return cookies

        except Exception as e:
            logger.error(f"[{self.site_name}] Error en HTTP login: {e}")
            return None

    # ─────────────────────────────────────────────
    # POSTULACIÓN PRINCIPAL
    # ─────────────────────────────────────────────

    async def apply(self, page, job_url, credentials=None):
        """
        Aplica a una oferta.

        Flujo:
          1. Login vía HTTP (aiohttp) → obtiene cookies de sesión
          2. Inyecta cookies en el contexto de Playwright
          3. Navega a la página del job → click APLICAR
             (ya logueado → va directo a /Jobs/Aplicar/{id})
          4. Selección de CV → preguntas → Gemini AI → submit

        Returns:
            (True,  None)            → Éxito
            (False, 'no_cumple: X') → No cumple requisitos
            (False, 'mensaje')       → Fallo técnico
        """
        creds = credentials or CREDENTIALS.get('tecoloco', {})

        try:
            logger.info(f"[{self.site_name}] Aplicando a: {job_url}")

            # ── Extraer job_id ──────────────────────────────────────
            match = re.search(r'/(\d{4,})/', job_url)
            if not match:
                return False, f"No se pudo extraer ID de: {job_url}"
            job_id = match.group(1)

            # ── Login vía HTTP (si no tenemos cookies cacheadas) ────
            if not self._session_cookies:
                cookies = await self._login_via_http(creds)
                if not cookies:
                    return False, "HTTP login falló — sin cookies de sesión"
                self._session_cookies = cookies

            # Inyectar cookies en el contexto del browser
            try:
                await page.context.add_cookies(self._session_cookies)
            except Exception as e:
                logger.warning(f"[{self.site_name}] Error inyectando cookies: {e}")

            # ── PASO 0: Ir a la página del job → click APLICAR ─────
            await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(2, 3))

            apply_btn = await page.query_selector(
                "a.apply-now, "
                "a[href*='Jobs/Aplicar'], "
                "a:has-text('Postularme')"
            )

            if apply_btn:
                href = await apply_btn.get_attribute("href") or ""
                logger.info(f"[{self.site_name}] Botón APLICAR encontrado: {href[:60]}")
                await apply_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(2, 3))
            else:
                logger.warning(f"[{self.site_name}] Botón APLICAR no encontrado, navegando directo")
                await page.goto(f"{BASE_URL}/Jobs/Aplicar/{job_id}", wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(random.uniform(1.5, 2.5))

            # ── Si aún en login → sesión inválida, limpiar cache ───
            if "login.aspx" in page.url.lower():
                self._session_cookies = []   # invalidar cache para próximo job
                return False, f"Sesión inválida tras click APLICAR. URL: {page.url}"

            # ── PASO 1: Selección de CV (/Jobs/Aplicar/{id}) ────────
            if "/jobs/aplicar/" in page.url.lower():

                cv_radio = await page.query_selector("input[name='CurriculoId']")
                if cv_radio and not await cv_radio.is_checked():
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
                    q_text = (await labels[i].inner_text()).strip() if i < len(labels) else ""
                    questions.append({"id": ta_id, "text": q_text})

                logger.info(f"[{self.site_name}] {len(questions)} preguntas → Gemini AI")

                if questions:
                    answers = await answer_questions(questions, profile)
                    for q in questions:
                        ans = answers.get(q["id"], "")
                        if ans:
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
        cookies = await self._login_via_http(credentials)
        if cookies:
            self._session_cookies = cookies
            await page.context.add_cookies(cookies)
            return True
        return False

    async def login_once(self, page, credentials=None) -> bool:
        return await self.login(page, credentials or CREDENTIALS.get('tecoloco', {}))
