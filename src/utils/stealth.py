"""
utils/stealth.py
────────────────
Utilidades de robustez técnica:
  - Rotación de User-Agent (20+ UAs reales 2024-2025)
  - Fingerprint aleatorio (viewport, timezone, plataforma)
  - Retry con backoff exponencial
  - Contexto de navegador stealth para Playwright
"""

import asyncio
import random
import logging
from functools import wraps

logger = logging.getLogger(__name__)

# ── User Agents reales (Chrome/Edge 2024-2025) ──────────────────────────────
USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    # Firefox Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Safari Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    # Chrome Android
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
]

# ── Viewport presets realistas ───────────────────────────────────────────────
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1680, "height": 1050},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1280, "height": 720},
    {"width": 1280, "height": 800},
    {"width": 1600, "height": 900},
]

# ── Locales y zonas horarias ─────────────────────────────────────────────────
LOCALES    = ["es-NI", "es-419", "es-GT", "es-HN", "es", "es-MX"]
TIMEZONES  = ["America/Managua", "America/Guatemala", "America/Tegucigalpa", "America/Mexico_City"]


def get_random_ua() -> str:
    """Devuelve un User-Agent aleatorio de la lista."""
    return random.choice(USER_AGENTS)


def get_random_fingerprint() -> dict:
    """
    Devuelve un fingerprint completo con viewport, locale y timezone aleatorios.
    Listo para pasarlo a playwright.new_context()
    """
    return {
        "user_agent": get_random_ua(),
        "viewport": random.choice(VIEWPORTS),
        "locale": random.choice(LOCALES),
        "timezone_id": random.choice(TIMEZONES),
        "color_scheme": "light",
        "extra_http_headers": {
            "Accept-Language": "es-NI,es;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
        }
    }


async def stealth_context(playwright, headless: bool = True, proxy_url: str = None):
    """
    Crea un browser + context de Playwright con fingerprint aleatorio y
    configuración stealth para evitar detección de bots.
    Retorna: (browser, context)
    """
    fp = get_random_fingerprint()
    launch_args = {
        "headless": headless,
        "args": [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-extensions",
        ]
    }
    if proxy_url:
        launch_args["proxy"] = {"server": proxy_url}
        logger.info(f"[Stealth] Usando proxy: {proxy_url}")

    browser = await playwright.chromium.launch(**launch_args)
    context = await browser.new_context(
        user_agent=fp["user_agent"],
        viewport=fp["viewport"],
        locale=fp["locale"],
        timezone_id=fp["timezone_id"],
        color_scheme=fp["color_scheme"],
        extra_http_headers=fp["extra_http_headers"],
        java_script_enabled=True,
        ignore_https_errors=True,
    )
    # Inyectar script que elimina navigator.webdriver
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });
        Object.defineProperty(navigator, 'languages', { get: () => ['es-NI','es','en'] });
    """)
    logger.debug(f"[Stealth] UA: {fp['user_agent'][:60]}... | Viewport: {fp['viewport']}")
    return browser, context


def retry_async(max_attempts: int = 3, base_delay: float = 30.0, backoff: float = 2.0):
    """
    Decorador para reintentar funciones async con backoff exponencial.
    Delay: base_delay → base_delay*backoff → base_delay*backoff² ...

    Uso:
        @retry_async(max_attempts=3, base_delay=30)
        async def mi_funcion(): ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts:
                        logger.error(f"[Retry] {func.__name__} falló tras {max_attempts} intentos: {e}")
                        raise
                    logger.warning(
                        f"[Retry] {func.__name__} intento {attempt}/{max_attempts} falló: {e}. "
                        f"Esperando {delay:.0f}s..."
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff
        return wrapper
    return decorator


async def human_delay(min_s: float = 1.5, max_s: float = 4.0):
    """Pausa aleatoria para simular comportamiento humano entre acciones."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def micro_delay(min_s: float = 0.3, max_s: float = 1.2):
    """Pausa corta entre acciones tipo scroll/click."""
    await asyncio.sleep(random.uniform(min_s, max_s))
