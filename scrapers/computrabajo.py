"""
scrapers/computrabajo.py — Scraper de ofertas en Computrabajo Nicaragua

Flujo:
  1. La búsqueda de ofertas usa curl_cffi (impersona TLS de Chrome real)
     para evadir el bloqueo 403 de Cloudflare desde IPs de datacenter.
  2. Por cada keyword se visita:
     https://ni.computrabajo.com/trabajo-de-{slug}
  3. Se paginan resultados con el parámetro ?p=N
  4. Se parsea cada <article> con BeautifulSoup4:
     - Título, URL, empresa, ubicación (formato "Ciudad, Departamento")
  5. Playwright SÍ se sigue usando en poster/computrabajo.py para aplicar
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

BASE_URL              = "https://ni.computrabajo.com"
# La página de login usa OAuth/PKCE con parámetros dinámicos.
# No se puede navegar directamente, hay que llegar desde el home:
# Home → click "Login" → click "Ingresar"
LOGIN_ENTRY_URL       = "https://ni.computrabajo.com"   # punto de entrada
MAX_PAGES_PER_KEYWORD = 5   # ~100 resultados máx por keyword (20 × 5)

# User-Agent que impersonará curl_cffi
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "es-NI,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "sec-ch-ua": '"Chromium";v="123", "Not:A-Brand";v="8"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "DNT": "1",
}

# Proxy Tor (SOCKS5) para evadir bloqueo de IP de datacenter en Cloudflare
# Tor debe estar corriendo en el VPS: apt install tor && systemctl start tor
TOR_PROXY = "socks5://127.0.0.1:9050"


def _to_slug(keyword: str) -> str:
    """
    Convierte keyword a slug sin tildes ni espacios.
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
    # Punto de entrada principal (sin Playwright)
    # ──────────────────────────────────────────

    async def scrape(self, playwright) -> list[dict]:
        """
        Scraping usando curl_cffi para evadir Cloudflare.
        playwright se recibe pero NO se usa en el scrape —
        solo se usa en poster/computrabajo.py para aplicar.
        """
        try:
            from curl_cffi.requests import AsyncSession
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error(
                "[computrabajo] curl_cffi o beautifulsoup4 no están instalados. "
                "Ejecuta: pip install curl_cffi beautifulsoup4"
            )
            return []

        keywords = await self._get_keywords()
        if not keywords:
            logger.warning(f"[{self.site_name}] Sin keywords activas para perfil {self.profile_id}")
            return []

        all_jobs = []

        async with AsyncSession() as session:
            for keyword in keywords:
                kw_jobs = await self._scrape_keyword_http(session, BeautifulSoup, keyword)
                all_jobs.extend(kw_jobs)
                await asyncio.sleep(random.uniform(2, 4))

        # Deduplicar por URL
        unique = {j["url"]: j for j in all_jobs}
        logger.info(f"[{self.site_name}] Total únicas antes de guardar: {len(unique)}")
        return list(unique.values())

    # ──────────────────────────────────────────
    # Búsqueda HTTP con curl_cffi
    # ──────────────────────────────────────────

    async def _scrape_keyword_http(self, session, BeautifulSoup, keyword: str) -> list[dict]:
        """
        Descarga páginas de resultados via HTTP con curl_cffi + Tor proxy
        para evadir el bloqueo de IP de datacenter en Cloudflare.
        """
        slug     = _to_slug(keyword)
        jobs     = []
        page_num = 1

        while page_num <= MAX_PAGES_PER_KEYWORD:
            if page_num == 1:
                url = f"{BASE_URL}/trabajo-de-{slug}"
            else:
                url = f"{BASE_URL}/trabajo-de-{slug}?p={page_num}"

            logger.info(f"[{self.site_name}] '{keyword}' pág.{page_num} → {url}")

            resp = None
            # Intentar primero con Tor, luego directo si Tor no está disponible
            for proxy in (TOR_PROXY, None):
                try:
                    kwargs = dict(
                        headers=_HEADERS,
                        impersonate="chrome120",
                        timeout=30,
                        allow_redirects=True,
                    )
                    if proxy:
                        kwargs["proxies"] = {"http": proxy, "https": proxy}
                        logger.debug(f"[{self.site_name}] Usando Tor proxy")
                    resp = await session.get(url, **kwargs)
                    break
                except Exception as e:
                    if proxy:
                        logger.warning(f"[{self.site_name}] Tor no disponible ({e}), probando sin proxy")
                    else:
                        logger.error(f"[{self.site_name}] Error HTTP '{keyword}' pág.{page_num}: {e}")
                    resp = None

            if resp is None:
                break

            if resp.status_code == 403:
                logger.warning(f"[{self.site_name}] 403 Forbidden en '{keyword}' pág.{page_num} (IP bloqueada por Cloudflare)")
                break
            if resp.status_code != 200:
                logger.warning(f"[{self.site_name}] HTTP {resp.status_code} en '{keyword}' pág.{page_num}")
                break

            soup     = BeautifulSoup(resp.text, "html.parser")
            articles = soup.find_all("article")
            logger.info(f"[{self.site_name}] '{keyword}' pág.{page_num}: {len(articles)} tarjetas")

            if not articles:
                title = soup.title.string if soup.title else "sin título"
                logger.warning(f"[{self.site_name}] Sin tarjetas. Título: '{title}'")
                break

            for article in articles:
                job = self._parse_article(article)
                if job:
                    jobs.append(job)

            # Paginación: si hay menos de 10 resultados → última página
            if len(articles) < 10:
                break

            page_num += 1
            await asyncio.sleep(random.uniform(1.5, 3))

        logger.info(f"[{self.site_name}] '{keyword}': {len(jobs)} ofertas en {page_num} página(s)")
        return jobs

    def _parse_article(self, article) -> dict | None:
        """Extrae datos de un <article> BeautifulSoup."""
        try:
            # Título y URL
            link = article.find("a", class_=re.compile(r"js-o-link|title"))
            if not link:
                link = article.find("h2") and article.find("h2").find("a")
            if not link:
                link = article.find("a", href=True)
            if not link:
                return None

            title = link.get_text(strip=True)
            href  = link.get("href", "")
            if not href:
                return None
            url = href if href.startswith("http") else BASE_URL + href

            # Empresa
            company_el = (
                article.find(class_=re.compile(r"company|empresa|org")) or
                article.find("span", class_=re.compile(r"fs16|fc_base"))
            )
            company = company_el.get_text(strip=True) if company_el else ""

            # Ubicación
            loc_el = (
                article.find(class_=re.compile(r"location|ubicacion|loc")) or
                article.find("span", class_=re.compile(r"fc_aux|location"))
            )
            location = loc_el.get_text(strip=True) if loc_el else ""

            if not title or not url:
                return None

            return {
                "title":    title,
                "url":      url,
                "company":  company,
                "location": location,
                "site":     "computrabajo",
                "salary":   "",
                "description": "",
                "requires_manual": False,
            }
        except Exception as e:
            logger.debug(f"[computrabajo] Error parseando article: {e}")
            return None

    # ──────────────────────────────────────────
    # Credenciales (usado por poster)
    # ──────────────────────────────────────────

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
