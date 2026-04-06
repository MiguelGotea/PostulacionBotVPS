import asyncio
import logging
from scrapers.base import BaseScraper
from config import PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class Encuentra24Scraper(BaseScraper):
    def __init__(self, profile_id: int = 1):
        super().__init__("encuentra24", profile_id)
        self.base_url = "https://www.encuentra24.com/"

    async def scrape(self, playwright) -> list[dict]:
        all_jobs = []
        browser, context = await self.get_browser_context(playwright)
        page = await context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        try:
            for keyword in await self._get_keywords():
                # URL: https://www.encuentra24.com/nicaragua-es/empleos?q=keyword.1
                search_url = f"{self.base_url}nicaragua-es/empleos?q={keyword}.1"
                
                logger.info(f"[{self.site_name}] Escaneando keyword: {keyword} -> {search_url}")
                
                try:
                    await page.goto(search_url, wait_until="domcontentloaded")
                    await self.human_delay()

                    # Selectores de Encuentra24
                    job_cards = await page.query_selector_all("article.ann-box-teaser, .ann-box-teaser")
                    
                    for card in job_cards[:10]:
                        try:
                            title_el = await card.query_selector(".ann-box-title, a[href*='/nicaragua-es/empleos/']")
                            title = await title_el.inner_text() if title_el else "Sin título"
                            
                            url = await title_el.get_attribute("href") if title_el else None
                            if url and not url.startswith("http"):
                                url = self.base_url.rstrip("/") + url

                            company = "Ver en sitio"
                            
                            if url:
                                all_jobs.append({
                                    'title': title.strip(),
                                    'company': company,
                                    'location': "Nicaragua",
                                    'url': url,
                                    'site': self.site_name,
                                    'requires_manual': True # Marcado como manual según requerimiento
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
