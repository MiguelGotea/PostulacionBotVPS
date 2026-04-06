import asyncio
import logging
import urllib.parse
from scrapers.base import BaseScraper
from config import PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class OpcionempleoScraper(BaseScraper):
    def __init__(self, profile_id: int = 1):
        super().__init__("opcionempleo", profile_id)
        self.base_url = "https://www.opcionempleo.com.ni/"

    async def scrape(self, playwright) -> list[dict]:
        all_jobs = []
        browser, context = await self.get_browser_context(playwright)
        page = await context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        try:
            for keyword in await self._get_keywords():
                # Ordenar por fecha para obtener los más recientes arriba
                query = urllib.parse.quote(keyword)
                # Opcionempleo: sort=date para prioridad cronológica
                search_url = f"{self.base_url}buscar/empleos?s={query}&l=Nicaragua&sort=date"
                
                logger.info(f"[{self.site_name}] Escaneando: {keyword} -> {search_url}")
                
                try:
                    await page.goto(search_url, wait_until="load")
                    await self.human_delay()

                    # Selectores ultra-amplios para Opcionempleo
                    # Buscamos cualquier artículo o div que parezca un job
                    job_cards = await page.query_selector_all("article, .job, .click, [data-job-id]")
                    
                    if not job_cards:
                        # Fallback a links de trabajo
                        job_cards = await page.query_selector_all("a[href*='/job/'], a[href*='/trabajo-']")

                    # Aumentamos a 40 el límite para capturar vacantes de días anteriores
                    for card in job_cards[:40]:
                        try:
                            # Título y Link
                            title_el = await card.query_selector("h2, .title, .job-title, a[href*='/job/']")
                            if not title_el: continue
                            
                            title = await title_el.inner_text()
                            
                            # Buscar el link en el elemento del título o en la tarjeta
                            url = await title_el.get_attribute("href")
                            if not url:
                                link_el = await card.query_selector("a")
                                if link_el: url = await link_el.get_attribute("href")

                            if url and not url.startswith("http"):
                                url = self.base_url.rstrip("/") + url

                            company_el = await card.query_selector(".company, .employer, .company_name, span[class*='company']")
                            company = await company_el.inner_text() if company_el else "Confidencial"

                            if url and ("/job/" in url.lower() or "trabajo-" in url.lower()):
                                all_jobs.append({
                                    'title': title.strip()[:100],
                                    'company': company.strip()[:100],
                                    'location': "Nicaragua",
                                    'url': url,
                                    'site': self.site_name,
                                    'requires_manual': False
                                })
                        except Exception as e:
                            logger.error(f"Error en tarjeta de {self.site_name}: {e}")
                            continue

                except Exception as e:
                    logger.error(f"Error en {keyword} en {self.site_name}: {e}")
                    continue

        finally:
            await browser.close()

        unique_jobs = {j['url']: j for j in all_jobs}.values()
        return list(unique_jobs)
