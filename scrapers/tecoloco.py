import asyncio
import logging
import random
import re
from scrapers.base import BaseScraper
from config import KEYWORDS, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

BASE_URL = "https://www.tecoloco.com.ni"
MAX_PAGES_PER_KEYWORD = 8   # máx 800 resultados por keyword (100 × 8)


class TecolocoScraper(BaseScraper):
    def __init__(self):
        super().__init__("tecoloco")
        self.base_url = BASE_URL

    # ──────────────────────────────────────────
    # Punto de entrada principal
    # ──────────────────────────────────────────

    async def scrape(self, playwright) -> list[dict]:
        all_jobs = []
        browser, context = await self.get_browser_context(playwright)
        page = await context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        try:
            for keyword in KEYWORDS:
                kw_jobs = await self._scrape_keyword(page, keyword)
                all_jobs.extend(kw_jobs)

        finally:
            await browser.close()

        # Eliminar duplicados por URL
        unique = {j['url']: j for j in all_jobs}
        logger.info(f"[{self.site_name}] Total únicas antes de guardar: {len(unique)}")
        return list(unique.values())

    # ──────────────────────────────────────────
    # Scraping de una keyword con paginación
    # ──────────────────────────────────────────

    async def _scrape_keyword(self, page, keyword: str) -> list[dict]:
        """Escanea todas las páginas de resultados para una keyword."""
        k_encoded = keyword.replace(" ", "+")
        # PerPage=100 → parámetro real confirmado en Tecoloco
        start_url  = f"{BASE_URL}/empleos?Keywords={k_encoded}&PaisId=41&PerPage=100"

        jobs        = []
        current_url = start_url
        page_num    = 1

        while page_num <= MAX_PAGES_PER_KEYWORD:
            logger.info(f"[{self.site_name}] '{keyword}' pág.{page_num} → {current_url}")

            try:
                await page.goto(current_url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(random.uniform(2, 4))
            except Exception as e:
                logger.error(f"[{self.site_name}] Error cargando pág.{page_num}: {e}")
                break

            cards = await page.query_selector_all("div.job-result")
            logger.info(f"[{self.site_name}] '{keyword}' pág.{page_num}: {len(cards)} tarjetas")

            if not cards:
                break

            for card in cards:
                job = await self._parse_card(card)
                if job:
                    jobs.append(job)

            # ── Paginación: buscar link "Siguiente" ──
            next_url = await self._get_next_page_url(page)
            if not next_url or len(cards) < 50:
                # Menos de 50 resultados = última página
                break

            current_url = next_url
            page_num   += 1
            await asyncio.sleep(random.uniform(1.5, 3))

        logger.info(f"[{self.site_name}] '{keyword}': {len(jobs)} ofertas en {page_num} página(s)")
        return jobs

    # ──────────────────────────────────────────
    # Detectar la URL de la siguiente página
    # ──────────────────────────────────────────

    async def _get_next_page_url(self, page) -> str | None:
        """
        Busca el link de 'Siguiente' o '>' en la paginación.
        Retorna la URL absoluta o None si no hay más páginas.
        """
        # Selectores habituales en tecoloco para el botón siguiente
        selectors = [
            "a[title='Siguiente']",
            "a:has-text('Siguiente')",
            "a:has-text('>')",
            ".paginacion a:last-child",
            ".pagination a[rel='next']",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
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
        try:
            # Título y link (selector real: h2 > a)
            link_el = await card.query_selector("h2 a")
            if not link_el:
                return None

            title = (await link_el.inner_text()).strip()
            href  = await link_el.get_attribute("href")
            if not href:
                return None

            url = href if href.startswith("http") else BASE_URL + href

            # Filtro: solo URLs con ID numérico de 4+ dígitos
            # /1062177/supervisor-de-ventas.aspx ✅  |  /empleo-categoria?... ❌
            if not re.search(r'/\d{4,}/', url):
                return None

            # Empresa (li con icono icon-building)
            company = "Confidencial"
            lis = await card.query_selector_all("ul li")
            for li in lis:
                icon = await li.query_selector("i.icon-building")
                if icon:
                    raw = (await li.inner_text()).strip()
                    company = raw.strip() or "Confidencial"
                    break

            return {
                'title':          title[:100],
                'company':        company[:100],
                'url':            url,
                'site':           self.site_name,
                'requires_manual': False
            }

        except Exception as e:
            logger.error(f"[{self.site_name}] Error parseando tarjeta: {e}")
            return None


# ── Test rápido ────────────────────────────────
if __name__ == "__main__":
    from playwright.async_api import async_playwright
    async def test():
        async with async_playwright() as p:
            s = TecolocoScraper()
            jobs = await s.scrape(p)
            for j in jobs[:5]:
                print(j)
            print(f"\nTotal: {len(jobs)}")
    asyncio.run(test())
