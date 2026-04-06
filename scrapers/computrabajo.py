"""
scrapers/computrabajo.py — Scraper de ofertas en Computrabajo Nicaragua

Flujo:
  1. Login con Playwright (2 pasos: email → continuar → password → iniciar)
  2. Por cada keyword se visita:
     https://ni.computrabajo.com/trabajo-de-{slug}
     (slug sin tildes generado con unicodedata)
  3. Se paginan los resultados con botón "Siguiente"
  4. Se parsea cada tarjeta <article>:
     - Título, URL, empresa, ubicación (formato "Ciudad, Departamento")
  5. Deduplicación por URL antes de guardar
"""
import asyncio
import logging
import random
import re
import unicodedata
import aiosqlite

from scrapers.base import BaseScraper
from config import PLAYWRIGHT_TIMEOUT, DB_PATH

logger = logging.getLogger(__name__)

BASE_URL       = "https://ni.computrabajo.com"
LOGIN_URL      = "https://ni.computrabajo.com/candidato/login"  # URL Nicaragua directa
LOGIN_URL_ALT  = "https://secure.computrabajo.com/Account/Login"  # fallback
MAX_PAGES_PER_KEYWORD = 5   # ~100 resultados máx por keyword (20 × 5)


def _to_slug(keyword: str) -> str:
    """
    Convierte una keyword a slug sin tildes ni espacios.
    "administración" → "administracion"
    "atención al cliente" → "atencion-al-cliente"
    """
    nfkd = unicodedata.normalize("NFKD", keyword)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", "-", ascii_str.strip()).lower()


class ComputrabajoScraper(BaseScraper):
    def __init__(self, profile_id: int = 1):
        super().__init__("computrabajo", profile_id)
        self.base_url = BASE_URL

    # ──────────────────────────────────────────
    # Browser context con anti-detección
    # ──────────────────────────────────────────


    async def get_browser_context(self, playwright):
        """
        Override del base: usa Firefox headless para evadir el bloqueo 403
        de Cloudflare. Firefox tiene un TLS fingerprint diferente a Chromium
        y es detectado con menor frecuencia como bot.
        """
        browser = await playwright.firefox.launch(
            headless=True,
            firefox_user_prefs={
                "general.useragent.override": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
                    "Gecko/20100101 Firefox/124.0"
                ),
                "intl.accept_languages": "es-NI, es, en-US, en",
                "dom.webdriver.enabled": False,           # oculta webdriver
                "useAutomationExtension": False,
                "permissions.default.image": 2,          # no cargar imágenes → más rápido
            }
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
                "Gecko/20100101 Firefox/124.0"
            ),
            viewport={"width": 1366, "height": 768},
            locale="es-NI",
            timezone_id="America/Managua",
            extra_http_headers={
                "Accept-Language": "es-NI,es;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "DNT": "1",
            }
        )
        # Init script: eliminar huella de automatización
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
                configurable: true
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['es-NI', 'es', 'en-US', 'en'],
            });
        """)
        return browser, context

    # ──────────────────────────────────────────
    # Punto de entrada principal
    # ──────────────────────────────────────────

    async def scrape(self, playwright) -> list[dict]:
        all_jobs = []
        browser, context = await self.get_browser_context(playwright)
        page = await context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        try:
            # Intentar login para ver el estado real (postulado o no)
            await self._login(page)

            keywords = await self._get_keywords()
            if not keywords:
                logger.warning(f"[{self.site_name}] Sin keywords activas para perfil {self.profile_id}")
                return []

            for keyword in keywords:
                kw_jobs = await self._scrape_keyword(page, keyword)
                all_jobs.extend(kw_jobs)

        finally:
            await browser.close()

        # Deduplicar por URL
        unique = {j["url"]: j for j in all_jobs}
        logger.info(f"[{self.site_name}] Total únicas antes de guardar: {len(unique)}")
        return list(unique.values())

    # ──────────────────────────────────────────
    # Login (2 pasos)
    # ──────────────────────────────────────────

    async def _login(self, page) -> bool:
        """
        Login de candidato en Computrabajo (flujo en 2 pasos).
        Prueba primero la URL Nicaragua y luego la global como fallback.
        """
        try:
            creds = await self._get_creds()
            if not creds.get("email") or not creds.get("password"):
                logger.warning(f"[{self.site_name}] Sin credenciales en DB, scrapeando sin sesión")
                return False

            # Intentar URL Nicaragua primero, luego fallback
            email_input = None
            for login_url in (LOGIN_URL, LOGIN_URL_ALT):
                logger.info(f"[{self.site_name}] Probando login en {login_url}")
                try:
                    await page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                    email_input = await page.query_selector(
                        "input#Email, input[type='email'], input[name='Email'], input[name='email']"
                    )
                    if email_input:
                        logger.info(f"[{self.site_name}] Formulario de login encontrado en {login_url}")
                        break
                    logger.warning(f"[{self.site_name}] Formulario no disponible en {login_url}")
                except Exception as ex:
                    logger.warning(f"[{self.site_name}] Error en {login_url}: {ex}")
                    continue

            if not email_input:
                logger.warning(f"[{self.site_name}] No se encontró formulario de login en ninguna URL")
                return False

            # ── Paso 1: email ──
            await email_input.fill(creds["email"])
            await asyncio.sleep(random.uniform(0.5, 1.0))

            continue_btn = await page.query_selector(
                "button#continueWithMailButton, "
                "button:has-text('Continuar'), "
                "button[type='submit']"
            )
            if continue_btn:
                await continue_btn.click()
                await asyncio.sleep(random.uniform(1.5, 2.5))

            # ── Paso 2: contraseña ──
            pwd_input = await page.query_selector("input#password, input[type='password']")
            if not pwd_input:
                logger.warning(f"[{self.site_name}] Campo de contraseña no encontrado")
                return False
            await pwd_input.fill(creds["password"])
            await asyncio.sleep(random.uniform(0.5, 1.0))

            submit_btn = await page.query_selector(
                "a#btnSubmitPass, "
                "button:has-text('Iniciar sesión'), "
                "button:has-text('Iniciar'), "
                "button[type='submit']"
            )
            if submit_btn:
                await submit_btn.click()
            else:
                await pwd_input.press("Enter")

            try:
                await page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            await asyncio.sleep(random.uniform(2, 3))

            # Verificar éxito: URL ya no es la de login
            url_actual = page.url.lower()
            if "account/login" not in url_actual and "candidato/login" not in url_actual:
                logger.info(f"[{self.site_name}] ✅ Login exitoso. URL: {page.url[:60]}")
                return True
            else:
                logger.warning(f"[{self.site_name}] Login falló. URL: {page.url[:60]}")
                return False

        except Exception as e:
            logger.error(f"[{self.site_name}] Error en _login: {e}")
            return False


    async def _get_creds(self) -> dict:
        """Lee credenciales del perfil activo desde la DB."""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT email, password FROM profile_credentials "
                    "WHERE profile_id = ? AND site_name = 'computrabajo'",
                    (self.profile_id,)
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        return {"email": row[0], "password": row[1]}
        except Exception as e:
            logger.error(f"[{self.site_name}] Error leyendo credenciales: {e}")
        return {}

    # ──────────────────────────────────────────
    # Scraping de una keyword con paginación
    # ──────────────────────────────────────────

    async def _scrape_keyword(self, page, keyword: str) -> list[dict]:
        """Escanea todas las páginas de resultados para una keyword."""
        slug        = _to_slug(keyword)
        start_url   = f"{BASE_URL}/trabajo-de-{slug}"
        current_url = start_url
        jobs        = []
        page_num    = 1

        while page_num <= MAX_PAGES_PER_KEYWORD:
            logger.info(f"[{self.site_name}] '{keyword}' pág.{page_num} → {current_url}")

            try:
                await page.goto(current_url, wait_until="domcontentloaded", timeout=60000)

                # ── CLAVE: esperar a que los article se rendericen (JS-rendered) ──
                # Computrabajo carga las tarjetas vía JS después del DOMContentLoaded
                try:
                    await page.wait_for_selector(
                        "article, #offersGridOfferContainer, .js-o-offer",
                        timeout=12000
                    )
                except Exception:
                    # Si no aparecen en 12s, probablemente CAPTCHA o página de error
                    logger.warning(f"[{self.site_name}] '{keyword}' pág.{page_num}: timeout esperando tarjetas")

                await asyncio.sleep(random.uniform(1.5, 2.5))
            except Exception as e:
                logger.error(f"[{self.site_name}] Error cargando pág.{page_num}: {e}")
                break

            # Cerrar posibles modales/banners de cookies o notificaciones
            await self._dismiss_overlays(page)

            cards = await page.query_selector_all("article")
            logger.info(f"[{self.site_name}] '{keyword}' pág.{page_num}: {len(cards)} tarjetas")

            if not cards:
                # Loguear el título de la página para diagnóstico
                title = await page.title()
                logger.warning(f"[{self.site_name}] Página sin tarjetas. Título: '{title}'")
                break

            for card in cards:
                job = await self._parse_card(card)
                if job:
                    jobs.append(job)

            # ── Paginación ──
            next_url = await self._get_next_page_url(page, current_url)
            if not next_url or len(cards) < 10:
                break

            current_url = next_url
            page_num   += 1
            await asyncio.sleep(random.uniform(1.5, 3))

        logger.info(f"[{self.site_name}] '{keyword}': {len(jobs)} ofertas en {page_num} página(s)")
        return jobs

    # ──────────────────────────────────────────
    # Cerrar overlays / modales molestos
    # ──────────────────────────────────────────

    async def _dismiss_overlays(self, page) -> None:
        """Intenta cerrar modales de cookies, notificaciones o banners de app."""
        selectors = [
            # Banner de cookies / GDPR
            "button:has-text('Aceptar')",
            "button:has-text('Acepto')",
            "button[data-name='cookie_accept']",
            # Modal de notificaciones push
            "button:has-text('Ahora no')",
            "button:has-text('No, gracias')",
            # Banner de descarga de app
            "button.close, button.js-close-banner, [aria-label='Close']",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.5)
            except Exception:
                continue

    # ──────────────────────────────────────────
    # URL de la siguiente página
    # ──────────────────────────────────────────

    async def _get_next_page_url(self, page, current_url: str) -> str | None:
        """
        Busca el link de 'Siguiente' en la paginación.
        En Computrabajo la paginación añade ?p=N a la URL base.
        """
        selectors = [
            "a:has-text('Siguiente')",
            "li.next a",
            "a[rel='next']",
            "a.js-next-page",
            ".pagination a:last-child",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    href = await el.get_attribute("href")
                    if href and href != "#" and "javascript" not in href.lower():
                        url = href if href.startswith("http") else BASE_URL + href
                        return url
            except Exception:
                continue
        return None

    # ──────────────────────────────────────────
    # Parsear una tarjeta individual
    # ──────────────────────────────────────────

    async def _parse_card(self, card) -> dict | None:
        """
        Extrae título, URL, empresa y ubicación de una tarjeta <article>.
        Formato de ubicación Computrabajo: "Ciudad, Departamento"
        Ejemplo: "Managua, Managua" | "León, León"
        """
        try:
            # ── Título y URL ──────────────────────────────────────────────
            link_el = await card.query_selector("h2 a.js-o-link, h2 a, a.js-o-link")
            if not link_el:
                return None

            title = (await link_el.inner_text()).strip()
            href  = await link_el.get_attribute("href")
            if not href or not title:
                return None

            url = href if href.startswith("http") else BASE_URL + href

            # Filtrar URLs que no sean ofertas reales
            if "/trabajo-de-" not in url and "/oferta-de-trabajo" not in url:
                # Aceptar también URLs del tipo /empleo-de-..., /trabajo/...
                if not re.search(r"/(empleo|oferta|trabajo)/", url, re.IGNORECASE):
                    pass  # Dejar pasar — la URL puede tener otros formatos

            # ── Empresa ──────────────────────────────────────────────────
            company = "Confidencial"
            for sel in [
                "p a.fc_base.t_ellipsis",
                "a[href*='/ofertas-de-trabajo/']",
                "p.fs16 a",
                "span.fc_base",
            ]:
                el = await card.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if text and text.lower() not in ("confidencial", ""):
                        company = text[:100]
                        break

            # Fallback: primer <p> de la tarjeta que no sea el título
            if company == "Confidencial":
                paras = await card.query_selector_all("p")
                for p in paras:
                    text = (await p.inner_text()).strip()
                    # Ignorar párrafos del salario (tienen "$") o fecha ("Hace/Ayer")
                    if text and "$" not in text and "hace" not in text.lower() and "ayer" not in text.lower():
                        company = text[:100]
                        break

            # ── Ubicación (formato: "Ciudad, Departamento") ───────────────
            location = ""
            # Buscar el texto que contiene una ciudad/departamento típico de NI
            # Lo detectamos porque suele aparecer en un <p> debajo de la empresa
            try:
                card_text = await card.inner_text()
                # Patrón: "Managua, Managua" | "León, León" | "Granada, Granada"
                # Los departamentos de Nicaragua
                depts = (
                    "Boaco|Carazo|Chinandega|Chontales|Estelí|Estelí|"
                    "Granada|Jinotega|León|Madriz|Managua|Masaya|Matagalpa|"
                    "Nueva Segovia|Río San Juan|Rivas|RAAN|RAAS"
                )
                m = re.search(
                    rf"[A-ZÁÉÍÓÚÑa-záéíóúñ\s]+,\s*({depts})",
                    card_text
                )
                if m:
                    # Extraer la parte completa "Ciudad, Departamento"
                    start = max(0, m.start() - 30)
                    fragment = card_text[start:m.end()].strip()
                    # Limpiar y quedarse con el formato "Ciudad, Departamento"
                    lines = [ln.strip() for ln in fragment.splitlines() if ln.strip()]
                    for line in reversed(lines):
                        if re.search(rf"({depts})", line):
                            location = line[:120]
                            break
            except Exception:
                pass

            # Fallback: buscar selector directo si Computrabajo usa una clase para ubicación
            if not location:
                for sel in [
                    "p span",            # "Managua, Managua" dentro de un span
                    "span.location",
                    "li:has(i.icon-map-marker)",
                ]:
                    el = await card.query_selector(sel)
                    if el:
                        text = (await el.inner_text()).strip()
                        if text and re.search(r",\s*\w", text):  # tiene coma → "Ciudad, Dept"
                            location = text[:120]
                            break

            return {
                "title":           title[:100],
                "company":         company[:100],
                "location":        location,
                "url":             url,
                "site":            self.site_name,
                "requires_manual": False,
            }

        except Exception as e:
            logger.error(f"[{self.site_name}] Error parseando tarjeta: {e}")
            return None


# ── Test rápido ────────────────────────────────
if __name__ == "__main__":
    from playwright.async_api import async_playwright

    async def test():
        async with async_playwright() as p:
            s = ComputrabajoScraper(profile_id=1)
            jobs = await s.scrape(p)
            for j in jobs[:5]:
                print(j)
            print(f"\nTotal: {len(jobs)}")

    asyncio.run(test())
