import asyncio
import logging
import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager

from config import DASHBOARD_PORT, CREDENTIALS
from db import init_db
from scheduler import start_scheduler
from dashboard.app import app as dashboard_app

# Configuración básica de logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler("logs/main.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Inicializar DB
    await init_db()
    
    # 2. Verificar credenciales
    missing_creds = [site for site, creds in CREDENTIALS.items() if not creds.get('email')]
    if missing_creds:
        logger.warning(f"Credenciales faltantes para: {', '.join(missing_creds)}. Algunos scrapers/posters no funcionarán.")
    
    # 3. Iniciar el Scheduler en segundo plano
    scheduler = start_scheduler()
    logger.info("Katty Jobs Scheduler iniciado.")
    
    yield
    
    # Shutdown
    scheduler.shutdown()
    logger.info("Katty Jobs Scheduler detenido.")

# Crear la aplicación principal (FastAPI)
# Usamos el dashboard_app montado o importado
app = dashboard_app
app.router.lifespan_context = lifespan

async def main():
    """Ejecuta el servidor con uvicorn."""
    config = uvicorn.Config(
        "main:app", 
        host="0.0.0.0", 
        port=DASHBOARD_PORT, 
        log_level="info",
        reload=False
    )
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Sistema detenido por el usuario.")
    except Exception as e:
        logger.critical(f"Error fatal en el sistema: {e}")
