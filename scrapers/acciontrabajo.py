import asyncio
import logging
import urllib.parse
import re
import unicodedata
from scrapers.base import BaseScraper
from config import PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

class AcciontrabajoScraper(BaseScraper):
    def __init__(self, profile_id: int = 1):
        super().__init__("acciontrabajo", profile_id)
        self.base_url = "https://ni.acciontrabajo.com/"

    def _slugify(self, text: str) -> str:
        """Convierte 'Atención al Cliente' en 'atencion-al-cliente'."""
        text = unicodedata.normalize('NFD', text).encode('ascii', 'ignore').decode('utf-8')
        text = text.lower()
        # Reemplazar espacios y caracteres no deseados con guiones
        text = re.sub(r'[^a-z0-9]+', '-', text).strip('-')
        return text

    async def scrape(self, playwright) -> list[dict]:
        all_jobs = []
        browser, context = await self.get_browser_context(playwright, proxy="socks5://127.0.0.1:9050")
        page = await context.new_page()
        # Timeout extendido para el VPS
        page.set_default_timeout(60000) 

        try:
            keywords = await self._get_keywords()
            if not keywords:
                logger.warning(f"[{self.site_name}] No se encontraron keywords para escaneo.")
                return []

            for keyword in keywords:
                logger.info(f"[{self.site_name}] Escaneando keyword: {keyword}")
                
                try:
                    # 1. Intentar URL amigable (SEO) - Formato: empleos-de-{slug}-en-nicaragua
                    slug = self._slugify(keyword)
                    search_url = f"{self.base_url}empleos-de-{slug}-en-nicaragua"
                    
                    logger.info(f"[{self.site_name}] Intentando URL SEO: {search_url}")
                    response = await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    
                    # 2. Si falla (403 o 404), intentar búsqueda tradicional con más tiempo
                    if not response or response.status >= 400:
                        logger.warning(f"[{self.site_name}] URL SEO falló (Status {response.status if response else 'N/A'}). Intentando vía buscador...")
                        await page.goto(self.base_url, wait_until="networkidle", timeout=60000)
                        
                        # Esperar al input de búsqueda
                        q_input = await page.wait_for_selector("input.q", timeout=30000)
                        if q_input:
                            await q_input.fill(keyword)
                            await page.fill("input.l", "Nicaragua")
                            await page.keyboard.press("Enter")
                            await page.wait_for_load_state("networkidle", timeout=60000)
                    
                    # Pausa para renderizado de tarjetas
                    await asyncio.sleep(4)

                    # Selector de ofertas: .vacancy_card
                    try:
                        await page.wait_for_selector(".vacancy_card", timeout=15000)
                    except:
                        pass # Si no aparece, quizás no hay resultados o usa el fallback h2

                    cards = await page.query_selector_all(".vacancy_card")
                    if not cards:
                        cards = await page.query_selector_all("h2")
                    
                    logger.info(f"[{self.site_name}] Encontradas {len(cards)} posibles ofertas para '{keyword}'")
                    
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
                            continue

                except Exception as e:
                    logger.error(f"[{self.site_name}] Error escaneando keyword '{keyword}': {e}")
                    continue

        finally:
            await browser.close()

        unique_jobs = {j['url']: j for j in all_jobs if j.get('url')}.values()
        logger.info(f"[{self.site_name}] Total final: {len(unique_jobs)} ofertas.")
        return list(unique_jobs)
