import asyncio
import logging
from scrapers.base import BaseScraper
from config import PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class AcciontrabajoScraper(BaseScraper):
    def __init__(self, profile_id: int = 1):
        super().__init__("acciontrabajo", profile_id)
        self.base_url = "https://ni.acciontrabajo.com/"

    async def scrape(self, playwright) -> list[dict]:
        all_jobs = []
        browser, context = await self.get_browser_context(playwright)
        page = await context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        try:
            keywords = await self._get_keywords()
            if not keywords:
                logger.warning(f"[{self.site_name}] No se encontraron keywords para escaneo.")
                return []

            for keyword in keywords:
                # URL búsqueda: https://ni.acciontrabajo.com/buscar?q={keyword}&l=Nicaragua
                search_url = f"{self.base_url}buscar?q={keyword.replace(' ', '+')}&l=Nicaragua"
                
                logger.info(f"[{self.site_name}] Escaneando keyword: {keyword} -> {search_url}")
                
                try:
                    await page.goto(search_url, wait_until="networkidle", timeout=60000)
                    await asyncio.sleep(3) # Esperar a que carguen los resultados AJAX

                    # Intentar detectar el contenedor de resultados o los h2 directamente
                    # A veces las ofertas están en un iframe o panel dinámico
                    job_elements = await page.query_selector_all("h2")
                    logger.info(f"[{self.site_name}] Encontrados {len(job_elements)} elementos H2")
                    
                    if not job_elements:
                        # Reintento con selectores alternativos
                        job_elements = await page.query_selector_all(".vacancy-item h2, .job-item h2, a.job-link")

                    for el in job_elements:
                        try:
                            title = (await el.inner_text()).strip()
                            if not title or len(title) < 4:
                                continue

                            # Link
                            url = await el.get_attribute("href")
                            if not url:
                                link_el = await el.query_selector("a")
                                if link_el: url = await link_el.get_attribute("href")
                            
                            if not url or "javascript" in url:
                                continue

                            if not url.startswith("http"):
                                url = self.base_url.rstrip("/") + (url if url.startswith("/") else f"/{url}")

                            # Ubicación y Empresa
                            location = "Nicaragua"
                            company = "Confidencial"
                            
                            # Intentar navegar hacia arriba al contenedor de la oferta
                            # Según la captura, la info está bajo el H2 o en hermanos
                            parent = await el.evaluate_handle("el => el.closest('div') || el.parentElement")
                            
                            # Buscar en el parent o hermanos del titulo
                            if parent:
                                b_el = await parent.query_selector("b")
                                if b_el:
                                    company = (await b_el.inner_text()).strip()
                                
                                # La ubicación suele ser el primer div que no sea el título
                                divs = await parent.query_selector_all("div")
                                for d in divs:
                                    txt = (await d.inner_text()).strip()
                                    if txt and "," in txt and len(txt) < 100:
                                        location = txt
                                        break

                            all_jobs.append({
                                'title': title[:100],
                                'company': company[:100],
                                'location': location[:120],
                                'url': url,
                                'site': self.site_name,
                                'requires_manual': False
                            })
                        except Exception as e:
                            logger.error(f"Error procesando elemento en {self.site_name}: {e}")
                            continue

                except Exception as e:
                    logger.error(f"Error escaneando {keyword} en {self.site_name}: {e}")
                    continue

        finally:
            await browser.close()

        # Eliminar duplicados y jobs sin URL
        unique_jobs = {j['url']: j for j in all_jobs if j.get('url')}.values()
        logger.info(f"[{self.site_name}] Total únicas encontradas: {len(unique_jobs)}")
        return list(unique_jobs)

if __name__ == "__main__":
    from playwright.async_api import async_playwright
    async def test():
        async with async_playwright() as p:
            s = AcciontrabajoScraper()
            jobs = await s.scrape(p)
            print(f"Encontrados {len(jobs)} empleos en {s.site_name}")
    asyncio.run(test())
