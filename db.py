import aiosqlite
import os
import asyncio
import logging
from config import DB_PATH

logger = logging.getLogger(__name__)

async def init_db():
    """Inicializa la base de datos y crea las tablas necesarias si no existen."""
    
    # Asegurar que el directorio data existe
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Tabla de empleos/ofertas
        await db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT,
                location TEXT,
                url TEXT UNIQUE NOT NULL,
                site TEXT NOT NULL,
                salary TEXT,
                description TEXT,
                date_found TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                date_applied TIMESTAMP,
                status TEXT DEFAULT 'new',
                requires_manual BOOLEAN DEFAULT 0,
                error_message TEXT
            )
        """)
        
        # Tabla de logs de escaneo
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                site TEXT,
                jobs_found INTEGER DEFAULT 0,
                jobs_applied INTEGER DEFAULT 0,
                errors TEXT
            )
        """)

        # Tabla de configuración de sitios (habilitar/deshabilitar)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS site_configs (
                site_name TEXT PRIMARY KEY,
                is_enabled BOOLEAN DEFAULT 1
            )
        """)
        
        # Poblar con sitios por defecto si está vacía
        default_sites = [
            'tecoloco', 'computrabajo', 'opcionempleo', 
            'acciontrabajo', 'encuentra24', 'linkedin'
        ]
        for site in default_sites:
            await db.execute("""
                INSERT OR IGNORE INTO site_configs (site_name, is_enabled) 
                VALUES (?, 1)
            """, (site,))
        
        await db.commit()
        logger.info("Base de datos inicializada correctamente.")

if __name__ == "__main__":
    # Script para inicializar manualmente si es necesario
    logging.basicConfig(level=logging.INFO)
    asyncio.run(init_db())
