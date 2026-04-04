import asyncio
import logging
import random
from scrapers.base import BaseScraper
from config import KEYWORDS, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class TecolocoScraper(BaseScraper):
    def __init__(self):
        super().__init__("tecoloco")
        self.base_url = "https://www.tecoloco.com.ni/"

    async def scrape(self, playwright) -> list[dict]:
        """Implementación específica para Tecoloco."""
        all_jobs = []
        browser, context = await self.get_browser_context(playwright)
        page = await context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        try:
            for keyword in KEYWORDS:
                # Construir URL formateada con guiones para Tecoloco
                k_slug = keyword.replace(" ", "-").lower()
                search_url = f"{self.base_url}listado/{k_slug}.aspx"
                
                logger.info(f"[{self.site_name}] Escaneando keyword: {keyword} -> {search_url}")
                
                try:
                    await page.goto(search_url, wait_until="networkidle")
                    await self.human_delay()

                    # Extraer tarjetas de oferta
                    # Nota: Estos selectores son ilustrativos y deben validarse con el DOM real
                    job_cards = await page.query_selector_all(".job-item, .job-listing, article.job")
                    
                    if not job_cards:
                        # Reintento con selector genérico si el anterior falla
                        job_cards = await page.query_selector_all("a[href*='/empleos/']")

                    for card in job_cards[:10]: # Limitar por keyword para no saturar
                        try:
                            title_el = await card.query_selector("h2, .title")
                            title = await title_el.inner_text() if title_el else "Sin título"
                            
                            url = await card.get_attribute("href")
                            if url and not url.startswith("http"):
                                url = self.base_url.rstrip("/") + url

                            # Navegar al detalle para obtener descripción completa
                            # (Opcional si la tarjeta ya tiene la info básica)
                            job = {
                                'title': title.strip(),
                                'url': url,
                                'site': self.site_name,
                                'requires_manual': False
                            }
                            
                            if url:
                                all_jobs.append(job)
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
