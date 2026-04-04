import asyncio
import logging
from scrapers.base import BaseScraper
from config import KEYWORDS, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class AcciontrabajoScraper(BaseScraper):
    def __init__(self):
        super().__init__("acciontrabajo")
        self.base_url = "https://ni.acciontrabajo.com/"

    async def scrape(self, playwright) -> list[dict]:
        all_jobs = []
        browser, context = await self.get_browser_context(playwright)
        page = await context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        try:
            for keyword in KEYWORDS:
                # URL búsqueda: https://ni.acciontrabajo.com/buscar?q={keyword}&l=Managua
                search_url = f"{self.base_url}buscar?q={keyword}&l=Managua"
                
                logger.info(f"[{self.site_name}] Escaneando keyword: {keyword} -> {search_url}")
                
                try:
                    await page.goto(search_url, wait_until="domcontentloaded")
                    await self.human_delay()

                    # Selectores de Acciontrabajo
                    job_cards = await page.query_selector_all(".job-item, .listing-item, li.job")
                    
                    for card in job_cards[:10]:
                        try:
                            title_el = await card.query_selector("h2 a, .title a, a.job-link")
                            title = await title_el.inner_text() if title_el else "Sin título"
                            
                            url = await title_el.get_attribute("href") if title_el else None
                            if url and not url.startswith("http"):
                                url = self.base_url.rstrip("/") + url

                            company_el = await card.query_selector(".company, .employer")
                            company = await company_el.inner_text() if company_el else "Confidencial"

                            if url:
                                all_jobs.append({
                                    'title': title.strip(),
                                    'company': company.strip(),
                                    'location': "Nicaragua",
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

        unique_jobs = {j['url']: j for j in all_jobs}.values()
        return list(unique_jobs)

if __name__ == "__main__":
    from playwright.async_api import async_playwright
    async def test():
        async with async_playwright() as p:
            s = AcciontrabajoScraper()
            jobs = await s.scrape(p)
            print(f"Encontrados {len(jobs)} empleos en {s.site_name}")
    asyncio.run(test())
