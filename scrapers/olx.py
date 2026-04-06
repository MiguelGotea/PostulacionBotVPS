"""
scrapers/olx.py — OLX Nicaragua (Sección Empleos)
───────────────────────────────────────────────────
Portal: https://www.olx.com.ni/trabajo-y-empleos/
Estado: SCRAPING STUB — estructura lista, flujo de postulación pendiente de entrenamiento.

Notas técnicas:
  - OLX Nicaragua es clasificados estilo HTML clásico
  - Anti-bot: muy bajo, sin Cloudflare en sección empleos
  - Sin sistema de postulación formal: las ofertas tienen email/teléfono
  - Modelo de postulación: responder con email al publicante
  - Alto volumen de ofertas locales de PYMES nicaragüenses
  - No requiere login para ver ofertas
  - Sin paginación estándar: scrolling infinito o paginación URL (?page=N)
"""

import logging
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

OLX_BASE       = "https://www.olx.com.ni"
OLX_JOBS_URL   = f"{OLX_BASE}/trabajo-y-empleos/"
OLX_SEARCH     = f"{OLX_BASE}/trabajo-y-empleos/?q={{}}"
OLX_LOGIN      = f"{OLX_BASE}/chat/login"    # Para responder a anuncios


class OLXScraper(BaseScraper):
    """
    Scraper para OLX.com.ni — sección Trabajo y Empleos.
    
    Particularidades:
      - No tiene postulación directa como Tecoloco/Computrabajo
      - El modelo de aplicación es: enviar mensaje/email al publicante
      - Útil para detectar vacantes de PYMES locales no publicadas en otros portales
      - requires_manual=True por defecto (requiere intervención humana para contactar)

    TODO (entrenamiento posterior):
      - Mapear selectores de tarjetas de empleo
      - Extraer: título, descripción, ubicación, fecha, contacto, URL
      - Implementar paginación
      - Opcional: automatizar envío de mensaje al publicante via OLX Chat
    """

    def __init__(self, profile_id: int = 1):
        super().__init__(site_name="olx", profile_id=profile_id)

    async def scrape(self, playwright) -> list[dict]:
        """
        [STUB] Búsqueda de clasificados de empleo en OLX Nicaragua.
        Retorna lista vacía hasta ser entrenado.
        """
        keywords = await self._get_keywords()
        if not keywords:
            logger.warning("[OLX] Sin keywords configuradas para perfil %d", self.profile_id)
            return []

        browser, context = await self.get_browser_context(playwright)
        jobs = []
        errors = None

        try:
            page = await context.new_page()

            for keyword in keywords:
                url = OLX_SEARCH.format(keyword.replace(" ", "+"))
                logger.info(f"[OLX] Buscando: '{keyword}' → {url}")
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await self.human_delay()

                # ── TODO: Implementar extracción de avisos ─────────────────
                # Selectores a determinar durante entrenamiento:
                # LIST_SELECTOR     = "li.EIR5N"                      # pendiente
                # TITLE_SELECTOR    = "span[data-testid='ad-title']"  # pendiente
                # PRICE_SELECTOR    = "span[data-testid='ad-price']"  # pendiente (salario)
                # LOCATION_SELECTOR = "span[data-testid='location']"  # pendiente
                # DATE_SELECTOR     = "span[data-testid='ad-date']"   # pendiente
                # LINK_SELECTOR     = "a.fhlkI"                       # pendiente
                # 
                # Nota: Todos los jobs de OLX se guardan con requires_manual=True
                # porque la postulación es manual (email/teléfono del publicante)
                # ──────────────────────────────────────────────────────────
                logger.info(f"[OLX] STUB activo — entrenamiento de selectores pendiente")
                break

        except Exception as e:
            errors = str(e)
            logger.error(f"[OLX] Error: {e}")
        finally:
            await browser.close()

        await self.log_scan(len(jobs), errors)
        logger.info(f"[OLX] Perfil {self.profile_id}: {len(jobs)} empleos encontrados")
        return jobs

    async def contact_advertiser(self, page, job_url: str, message: str) -> bool:
        """
        [TODO] Implementar envío de mensaje al publicante en OLX.
        Flujo esperado:
          1. Ir al detalle del anuncio (job_url)
          2. Click en "Enviar mensaje" / "Llamar" / "Mostrar teléfono"
          3. Si es mensaje: escribir el mensaje del candidato y enviar
          4. Registrar como requires_manual=False si se envió automáticamente
          
        Nota: OLX permite mensajes sin login en algunos casos.
        El mensaje debe incluir el nombre del candidato y una breve presentación.
        """
        logger.info(f"[OLX] contact_advertiser pendiente: {job_url}")
        return False
