import asyncio
import logging
from scrapers.base import BaseScraper
from config import KEYWORDS, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class ComputrabajoScraper(BaseScraper):
    def __init__(self):
        super().__init__("computrabajo")
        self.base_url = "https://ni.computrabajo.com/"

    async def scrape(self, playwright) -> list[dict]:
        all_jobs = []
        browser, context = await self.get_browser_context(playwright)
        page = await context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        try:
            for keyword in KEYWORDS:
                # URL: https://ni.computrabajo.com/trabajo-de-{keyword}-en-managua
                k_slug = keyword.replace(" ", "-").lower()
                search_url = f"{self.base_url}trabajo-de-{k_slug}-en-managua"
                
                logger.info(f"[{self.site_name}] Escaneando keyword: {keyword} -> {search_url}")
                
                try:
                    await page.goto(search_url, wait_until="domcontentloaded")
                    await self.human_delay()

                    # Selectores de computrabajo
                    # Se busca h1.title o .js-o-link en las tarjetas
                    job_cards = await page.query_selector_all("article.box_offer, .bRS.js-item")
                    
                    for card in job_cards[:10]:
                        try:
                            title_el = await card.query_selector("h1 a, .js-o-link")
                            title = await title_el.inner_text() if title_el else "Sin título"
                            
                            url = await title_el.get_attribute("href") if title_el else None
                            if url and not url.startswith("http"):
                                url = self.base_url.rstrip("/") + url

                            company_el = await card.query_selector(".it-el.fs16, a[href*='/ofertas-de-trabajo/'] span")
                            company = await company_el.inner_text() if company_el else "Confidencial"
                            
                            salary_el = await card.query_selector("span[data-salary]")
                            salary = await salary_el.inner_text() if salary_el else "No especificado"

                            if url:
                                all_jobs.append({
                                    'title': title.strip(),
                                    'company': company.strip(),
                                    'location': "Managua",
                                    'url': url,
                                    'site': self.site_name,
                                    'salary': salary.strip(),
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
            s = ComputrabajoScraper()
            jobs = await s.scrape(p)
            print(f"Encontrados {len(jobs)} empleos en {s.site_name}")
    asyncio.run(test())
