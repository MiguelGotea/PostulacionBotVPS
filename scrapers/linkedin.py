import asyncio
import logging
import urllib.parse
from scrapers.base import BaseScraper
from config import KEYWORDS, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class LinkedinScraper(BaseScraper):
    def __init__(self):
        super().__init__("linkedin")
        self.base_url = "https://www.linkedin.com/jobs/search/"

    async def scrape(self, playwright) -> list[dict]:
        all_jobs = []
        browser, context = await self.get_browser_context(playwright)
        page = await context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        try:
            for keyword in KEYWORDS:
                # URL: https://www.linkedin.com/jobs/search/?location=Nicaragua&keywords={keyword}
                query = urllib.parse.quote(keyword)
                search_url = f"{self.base_url}?location=Nicaragua&keywords={query}"
                
                logger.info(f"[{self.site_name}] Escaneando keyword: {keyword} -> {search_url}")
                
                try:
                    # LinkedIn a menudo bloquea bots sin login, intentar con cuidado
                    await page.goto(search_url, wait_until="domcontentloaded")
                    await self.human_delay()

                    # Verificar si nos mandó a login
                    if "authwall" in page.url or "login" in page.url:
                        logger.warning(f"[{self.site_name}] Detectada barrera de login (authwall). Saltando {keyword}.")
                        continue

                    job_cards = await page.query_selector_all(".base-card, .jobs-search__results-list li")
                    
                    for card in job_cards[:5]:
                        try:
                            title_el = await card.query_selector("h3, .base-search-card__title")
                            title = await title_el.inner_text() if title_el else "Sin título"
                            
                            url_el = await card.query_selector("a.base-card__full-link, a[href*='/jobs/view/']")
                            url = await url_el.get_attribute("href") if url_el else None
                            
                            company_el = await card.query_selector("h4, .base-search-card__subtitle")
                            company = await company_el.inner_text() if company_el else "Confidencial"

                            if url:
                                all_jobs.append({
                                    'title': title.strip(),
                                    'company': company.strip(),
                                    'location': "Nicaragua",
                                    'url': url,
                                    'site': self.site_name,
                                    'requires_manual': True # Siempre manual para LinkedIn
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
