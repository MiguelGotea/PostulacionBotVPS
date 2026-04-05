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
import aiosqlite

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

    async def _load_cookies_from_db(self) -> list[dict] | None:
        """
        Lee las cookies de sesión de Tecoloco almacenadas en la DB.
        Estas cookies fueron obtenidas desde el navegador local del usuario
        (IP residencial) para evitar el bloqueo de DigitalOcean en login.aspx.
        
        Retorna lista de cookies para inyectar en Playwright, o None si no hay.
        """
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT cookies FROM session_cookies WHERE site = 'tecoloco'",
                ) as cursor:
                    row = await cursor.fetchone()

            if not row or not row[0]:
                logger.warning(f"[{self.site_name}] Sin cookies en DB. Usa el endpoint /api/session para cargarlas.")
                return None

            raw = row[0]

            # Parsear el header de cookies ("name=value; name2=value2; ...")
            cookie_list = []
            for part in raw.split(";"):
                part = part.strip()
                if "=" not in part:
                    continue
                name, _, value = part.partition("=")
                name = name.strip()
                value = value.strip()
                if name:
                    cookie_list.append({
                        "name":   name,
                        "value":  value,
                        "domain": "www.tecoloco.com.ni",
                        "path":   "/",
                    })

            logger.info(f"[{self.site_name}] {len(cookie_list)} cookies cargadas de DB")
            return cookie_list if cookie_list else None

        except Exception as e:
            logger.error(f"[{self.site_name}] Error leyendo cookies de DB: {e}")
            return None

    async def _check_department(self, location: str) -> str | None:
        """
        Verifica si la ubicación de la oferta está en los departamentos
        permitidos para el perfil activo.

        Returns:
            None                           → OK, puede aplicar
            'sin_departamento: ...'        → Solo dice Nicaragua, no especifica
            'departamento_no_permitido: X' → Departamento fuera del filtro
        """
        if not location:
            return None  # Sin location → no se filtra (dejar pasar)

        loc_lower = location.lower().strip()

        # Si solo dice "Nicaragua" sin especificar ciudad/departamento
        if loc_lower in ("nicaragua", "nicaragua."):
            return "sin_departamento: La oferta no especifica departamento"

        # Extraer el departamento: "Managua, Nicaragua" → "Managua"
        # También manejar "Managua" sin coma
        dept_raw = location.split(",")[0].strip()

        # Leer departamentos habilitados del perfil activo
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
            return None  # Si falla la DB, dejar pasar

        if not allowed:
            return None  # Sin configuración → no filtrar

        if dept_raw.lower() in allowed:
            return None  # ✅ Departamento permitido

        return f"departamento_no_permitido: {dept_raw}"

    async def _update_company_from_page(self, page, job_id) -> None:
        """
        Lee el nombre real de la empresa desde la página del job
        y actualiza la DB si estaba guardado como 'Confidencial'.
        """
        if not job_id:
            return
        try:
            company = None
            # Selectores en la página de detalle de Tecoloco
            for sel in [
                ".employer-name a",
                ".company-name",
                "a[href*='empresa']",
                ".job-detail-company",
                "h2.employer a",
                # Sidebar izquierdo — nombre de empresa grande
                ".sidebar .employer",
                "aside h3",
                # Link junto al título del puesto
                "h1 + p a, h1 + div a",
            ]:
                el = await page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if text and text.lower() not in ("confidencial", ""):
                        company = text[:100]
                        break

            # Si aún no encontramos, buscar el patrón en el HTML del site
            if not company:
                try:
                    # Tecoloco pone el nombre en un <a> con clase o cerca del logo
                    els = await page.query_selector_all("section a, .job-info a, article a")
                    for el in els:
                        href = await el.get_attribute("href") or ""
                        text = (await el.inner_text()).strip()
                        if (text and len(text) > 3
                                and text.lower() not in ("confidencial", "ver oferta", "aplicar")
                                and not href.startswith("/empleos") and not href.startswith("/login")):
                            company = text[:100]
                            break
                except Exception:
                    pass

            if company:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE jobs SET company = ? WHERE id = ? AND (company = 'Confidencial' OR company IS NULL)",
                        (company, job_id)
                    )
                    if db.total_changes:
                        logger.info(f"[{self.site_name}] Empresa actualizada: '{company}' (id={job_id})")
                    await db.commit()
        except Exception as e:
            logger.debug(f"[{self.site_name}] _update_company_from_page: {e}")

    async def _login_via_http(self, credentials: dict) -> list[dict] | None:
        """Fallback: login vía HTTP POST (sin browser headless)."""
        import aiohttp as aio
        email    = credentials.get('email', '')
        password = credentials.get('password', '')
        try:
            jar = aio.CookieJar(unsafe=True)
            async with aio.ClientSession(cookie_jar=jar, headers=_HEADERS) as session:
                async with session.get(LOGIN_URL) as resp:
                    html = await resp.text()
                vs_match  = re.search(r'id="__VIEWSTATE"\s+value="([^"]*)"', html)
                evv_match = re.search(r'id="__EVENTVALIDATION"\s+value="([^"]*)"', html)
                vsg_match = re.search(r'id="__VIEWSTATEGENERATOR"\s+value="([^"]*)"', html)
                if not vs_match:
                    logger.error(f"[{self.site_name}] HTTP login: página sin __VIEWSTATE (posible bloqueo IP)")
                    return None
                form_data = {
                    "Email": email, "Password": password,
                    "loginButton": "INICIO CANDIDATOS",
                    "__VIEWSTATE":          vs_match.group(1),
                    "__EVENTVALIDATION":    evv_match.group(1) if evv_match else "",
                    "__VIEWSTATEGENERATOR": vsg_match.group(1) if vsg_match else "",
                }
                async with session.post(LOGIN_URL, data=form_data, headers={
                    **_HEADERS, "Content-Type": "application/x-www-form-urlencoded", "Referer": LOGIN_URL
                }, allow_redirects=True) as resp:
                    final_url = str(resp.url)
                if "login.aspx" in final_url.lower():
                    logger.error(f"[{self.site_name}] HTTP login falló. URL: {final_url}")
                    return None
                cookies = [{"name": c.key, "value": c.value, "domain": "www.tecoloco.com.ni", "path": "/"}
                           for c in jar]
                logger.info(f"[{self.site_name}] ✅ HTTP Login exitoso ({len(cookies)} cookies)")
                return cookies
        except Exception as e:
            logger.error(f"[{self.site_name}] Error en HTTP login: {e}")
            return None

    # ─────────────────────────────────────────────
    # POSTULACIÓN PRINCIPAL
    # ─────────────────────────────────────────────

    async def apply(self, page, job_url, credentials=None, job_db_id=None):
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

            # ── Filtro de departamento ─────────────────────────────────
            location = job.get('location', '') if isinstance(job, dict) else ''
            if not location:
                # Leer location de la DB si no viene en el dict
                try:
                    async with aiosqlite.connect(DB_PATH) as _db:
                        async with _db.execute("SELECT location FROM jobs WHERE id=?", (job_db_id,)) as _cur:
                            _row = await _cur.fetchone()
                            if _row:
                                location = _row[0] or ''
                except Exception:
                    pass

            dept_result = await self._check_department(location)
            if dept_result is not None:
                # dept_result es el status code a retornar
                logger.info(f"[{self.site_name}] {dept_result}: {location!r}")
                return False, dept_result

            # ── Cargar cookies (DB primario → HTTP fallback) ────────
            if not self._session_cookies:
                # 1. Intenta cookies de DB (sesión del navegador local)
                cookies = await self._load_cookies_from_db()
                # 2. Fallback: login HTTP (funciona si el IP no está bloqueado)
                if not cookies:
                    cookies = await self._login_via_http(creds)
                if not cookies:
                    return False, "No hay cookies de sesión. Usa /api/session para cargarlas desde tu navegador."
                self._session_cookies = cookies

            # Inyectar cookies en el contexto del browser
            try:
                await page.context.add_cookies(self._session_cookies)
            except Exception as e:
                logger.warning(f"[{self.site_name}] Error inyectando cookies: {e}")

            # ── PASO 0: Ir a la página del job → leer empresa real → click APLICAR ─
            await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(2, 3))

            # Leer nombre real de empresa (puede estar oculto en el card de búsqueda)
            await self._update_company_from_page(page, job_db_id)

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

            # ── Ya aplicado anteriomente → éxito silencioso ─────────
            try:
                body_text = await page.inner_text("body")
                ya_frases = ("ya has aplicado", "ya aplicaste", "ya postulaste",
                             "ya has postulado", "ya te postulaste", "already applied")
                if any(f in body_text.lower() for f in ya_frases):
                    logger.info(f"[{self.site_name}] ✅ Job ya aplicado anteriormente (detectado en job page)")
                    return True, None
            except Exception:
                pass

            # ── PASO 1: Selección de CV (/Jobs/Aplicar/{id}) ────────
            if "/jobs/aplicar/" in page.url.lower():

                cv_radio = await page.query_selector("input[name='CurriculoId']")
                if cv_radio and not await cv_radio.is_checked():
                    await cv_radio.click()
                    await asyncio.sleep(0.5)

                # El criterio REAL de bloqueo es la AUSENCIA del botón de aplicar,
                # NO el texto del alert (Tecoloco muestra avisos de requisitos opcionales
                # aunque puedas aplicar igual).
                go_btn = await page.query_selector(
                    "button:has-text('APLICAR A ESTA OFERTA'), "
                    "a:has-text('APLICAR A ESTA OFERTA'), "
                    "button#goToQuestions, "
                    "button:has-text('IR A PREGUNTAS'), "
                    "a:has-text('IR A PREGUNTAS'), "
                    "button:has-text('APLICAR'), "
                    "button#applyButton, "
                    "input[type='submit']"
                )

                if not go_btn:
                    # Solo ahora es un no_cumple real → extraer razón del alert
                    motivo = "Sin botón APLICAR en la página"
                    for sel in [".alert-danger", ".alert-warning", ".alert"]:
                        alert_el = await page.query_selector(sel)
                        if alert_el:
                            texto = (await alert_el.inner_text()).strip().lower()
                            if "no cumple" in texto or "requisito" in texto or "bloqueado" in texto:
                                motivo = re.sub(r'\s+', ' ', texto)[:300]
                                break
                    logger.info(f"[{self.site_name}] no_cumple: {motivo[:80]}")
                    return False, f"no_cumple: {motivo}"

                btn_text = (await go_btn.inner_text()).strip()
                logger.info(f"[{self.site_name}] Botón '{btn_text}' encontrado → aplicando")
                await go_btn.click()
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=30000)
                except Exception:
                    pass  # Continuar aunque el load_state tarde
                await asyncio.sleep(random.uniform(2, 3))

                # ── Ya aplicado → detectar en la página de selección de CV ───
                try:
                    body_text = await page.inner_text("body")
                    ya_frases = ("ya has aplicado", "ya aplicaste", "ya postulaste",
                                 "ya has postulado", "ya te postulaste", "already applied")
                    if any(f in body_text.lower() for f in ya_frases):
                        logger.info(f"[{self.site_name}] ✅ Job ya aplicado (detectado en /Jobs/Aplicar/)")
                        return True, None
                except Exception:
                    pass

                # ── ApplyToJobOffer inmediato (sin preguntas) ──────────
                if "applytojob" in page.url.lower():
                    logger.info(f"[{self.site_name}] ✅ Postulación directa → {page.url[:80]}")
                    return True, None

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
                    return False, f"Botón APLICAR final no encontrado en preguntas. URL: {page.url}"

                await submit_btn.click()
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=30000)
                except Exception:
                    pass
                await asyncio.sleep(3)

            # ── PASO 3: Verificar resultado ─────────────────────────
            final_url = page.url.lower()

            # URLs de éxito conocidas:
            # - /Jobs/ApplyToJobOffer?jobId=...  (aplicación directa sin preguntas)
            # - /Jobs/ApplyQuestions/...         (después de responder preguntas)
            success_urls = ("applytojob", "confirmacion", "gracias", "aplicacion-enviada")
            is_success = any(s in final_url for s in success_urls)

            # También verificar texto de confirmación en la página
            if not is_success:
                try:
                    body_text = await page.inner_text("body")
                    if "hemos enviado" in body_text.lower() or "aplicación enviada" in body_text.lower():
                        is_success = True
                except Exception:
                    pass

            if "login.aspx" in final_url:
                self._session_cookies = []   # sesión expiró
                return False, f"Sesión expiró tras submit. URL: {page.url}"

            if not is_success and "error" in final_url:
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
