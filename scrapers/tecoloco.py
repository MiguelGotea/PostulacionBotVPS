import asyncio
import logging
import random
import re
from scrapers.base import BaseScraper
from config import KEYWORDS, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

BASE_URL = "https://www.tecoloco.com.ni"

class TecolocoScraper(BaseScraper):
    def __init__(self):
        super().__init__("tecoloco")
        self.base_url = BASE_URL

    async def scrape(self, playwright) -> list[dict]:
        all_jobs = []
        browser, context = await self.get_browser_context(playwright)
        page = await context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        try:
            for keyword in KEYWORDS:
                k_encoded = keyword.replace(" ", "+")
                search_url = f"{BASE_URL}/empleos?Keywords={k_encoded}&PaisId=41"
                logger.info(f"[{self.site_name}] Escaneando keyword: {keyword} -> {search_url}")

                try:
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(random.uniform(2, 4))

                    # Selector real confirmado: div.job-result
                    job_cards = await page.query_selector_all("div.job-result")
                    logger.info(f"[{self.site_name}] '{keyword}': {len(job_cards)} tarjetas encontradas")

                    for card in job_cards[:25]:
                        try:
                            # Título y link: h2 > a (selector real confirmado)
                            link_el = await card.query_selector("h2 a")
                            if not link_el:
                                continue

                            title = (await link_el.inner_text()).strip()
                            href = await link_el.get_attribute("href")
                            if not href:
                                continue

                            # Construir URL absoluta
                            if href.startswith("http"):
                                url = href
                            else:
                                url = BASE_URL + href

                            # Filtro: solo URLs con ID numérico de 4+ dígitos
                            # Ej: /1062177/supervisor-de-ventas.aspx ✅
                            # Ej: /empleo-marketing-ventas?Keywords=... ❌
                            if not re.search(r'/\d{4,}/', url):
                                continue

                            # Empresa: buscar en los <li> de la tarjeta
                            company = "Confidencial"
                            lis = await card.query_selector_all("ul li")
                            for li in lis:
                                icon = await li.query_selector("i.icon-building")
                                if icon:
                                    raw = (await li.inner_text()).strip()
                                    # Eliminar posibles caracteres del icono al inicio
                                    company = raw.lstrip("0123456789 \t\n").strip() or raw
                                    break

                            all_jobs.append({
                                'title': title[:100],
                                'company': company[:100],
                                'url': url,
                                'site': self.site_name,
                                'requires_manual': False
                            })

                        except Exception as e:
                            logger.error(f"[{self.site_name}] Error en tarjeta: {e}")
                            continue

                except Exception as e:
                    logger.error(f"[{self.site_name}] Error escaneando '{keyword}': {e}")
                    continue

        finally:
            await browser.close()

        # Eliminar duplicados por URL
        unique = {j['url']: j for j in all_jobs}
        logger.info(f"[{self.site_name}] Total únicas antes de guardar: {len(unique)}")
        return list(unique.values())


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
