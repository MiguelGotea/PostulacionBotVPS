import asyncio
import logging
import random
import re
from scrapers.base import BaseScraper
from config import KEYWORDS, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class TecolocoScraper(BaseScraper):
    def __init__(self):
        super().__init__("tecoloco")
        self.base_url = "https://www.tecoloco.com.ni/"

    async def scrape(self, playwright) -> list[dict]:
        all_jobs = []
        browser, context = await self.get_browser_context(playwright)
        page = await context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        try:
            for keyword in KEYWORDS:
                # Usar la URL de búsqueda directa que es más confiable
                k_query = keyword.replace(" ", "+").lower()
                search_url = f"{self.base_url}empleos?Keywords={k_query}&PaisId=41"
                
                logger.info(f"[{self.site_name}] Escaneando keyword: {keyword} -> {search_url}")
                
                try:
                    await page.goto(search_url, wait_until="load")
                    await self.human_delay()

                    # Extraer tarjetas de oferta (Selectores ultra-agresivos)
                    job_cards = await page.query_selector_all(".job-result, .card, .job-item, .listing-card, [class*='job']")
                    
                    if not job_cards:
                        # Reintento con cualquier link que parezca una oferta
                        job_cards = await page.query_selector_all("a[href*='empleos/'], a[href*='ofertas-de-trabajo/']")

                    for card in job_cards[:20]:
                        try:
                            # Buscar el título principal
                            title_el = await card.query_selector("h2, .title, .job-title, strong")
                            title = await title_el.inner_text() if title_el else "Oferta de Empleo"
                            
                            # Buscar el link
                            url = await card.get_attribute("href")
                            if not url:
                                link_el = await card.query_selector("a")
                                if link_el: url = await link_el.get_attribute("href")

                            if url and not url.startswith("http"):
                                url = self.base_url.rstrip("/") + url

                            company_el = await card.query_selector(".company, .employer, [class*='empresa']")
                            company = await company_el.inner_text() if company_el else "Confidencial"

                            # Solo aceptar URLs con ID numérico real (ej: /1054667/...)
                            if url and re.search(r'/\d+/', url):
                                all_jobs.append({
                                    'title': title.strip()[:100],
                                    'company': company.strip()[:100],
                                    'url': url,
                                    'site': self.site_name,
                                    'requires_manual': False
                                })
                        except Exception as e:
                            logger.error(f"Error procesando tarjeta en {self.site_name}: {e}")
                            continue

                except Exception as e:
                    logger.error(f"Error escaneando {keyword} en {self.site_name}: {e}")
                    continue

        finally:
            await browser.close()

        # Eliminar duplicados por URL antes de retornar
        unique_jobs = {j['url']: j for j in all_jobs}.values()
        return list(unique_jobs)

if __name__ == "__main__":
    from playwright.async_api import async_playwright
    async def test():
        async with async_playwright() as p:
            s = TecolocoScraper()
            jobs = await s.scrape(p)
            print(f"Encontrados {len(jobs)} empleos")
    asyncio.run(test())
