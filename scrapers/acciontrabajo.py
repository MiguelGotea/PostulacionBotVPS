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
            import urllib.parse
            keywords = await self._get_keywords()
            if not keywords:
                logger.warning(f"[{self.site_name}] No se encontraron keywords para escaneo.")
                return []

            for keyword in keywords:
                logger.info(f"[{self.site_name}] Escaneando keyword: {keyword}")
                
                try:
                    # Ir al home para usar el buscador real y evitar 404s en URLs directas
                    await page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(2)
                    
                    # Rellenar keywords (input class="q")
                    await page.fill("input.q", keyword)
                    # Rellenar ubicación (input class="l")
                    await page.fill("input.l", "Nicaragua")
                    # Enter en location o click en gosearch
                    await page.press("input.l", "Enter")
                    
                    await page.wait_for_load_state("networkidle", timeout=60000)
                    await asyncio.sleep(3)
                    
                    # Selector confirmado: .vacancy_card
                    cards = await page.query_selector_all(".vacancy_card")
                    logger.info(f"[{self.site_name}] Encontradas {len(cards)} tarjetas .vacancy_card")
                    
                    if not cards:
                        # Fallback a buscar h2 si .vacancy_card no aparece
                        cards = await page.query_selector_all("h2")

                    for card in cards:
                        try:
                            # 1. Título y URL
                            title_el = await card.query_selector("h2")
                            if not title_el and (await card.evaluate("el => el.tagName")) == "H2":
                                title_el = card
                            
                            if not title_el:
                                continue

                            title = (await title_el.inner_text()).strip()
                            if not title or len(title) < 4:
                                continue

                            link_el = await card.query_selector("a")
                            if not link_el and (await card.evaluate("el => el.tagName")) == "A":
                                link_el = card
                            
                            url = await link_el.get_attribute("href") if link_el else None
                            if not url:
                                continue

                            if not url.startswith("http"):
                                url = self.base_url.rstrip("/") + (url if url.startswith("/") else f"/{url}")

                            # 2. Ubicación y Empresa
                            location = "Nicaragua"
                            company = "Confidencial"
                            
                            company_el = await card.query_selector("b")
                            if company_el:
                                company = (await company_el.inner_text()).strip()
                            
                            loc_el = await card.query_selector("div")
                            if loc_el:
                                location = (await loc_el.inner_text()).strip()

                            all_jobs.append({
                                'title': title[:100],
                                'company': company[:100],
                                'location': location[:120],
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

        unique_jobs = {j['url']: j for j in all_jobs if j.get('url')}.values()
        logger.info(f"[{self.site_name}] Total únicas encontradas: {len(unique_jobs)}")
        return list(unique_jobs)

