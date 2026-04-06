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
                profile_id INTEGER DEFAULT 1,
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

        # Migración: agregar profile_id si no existe (para DB existentes)
        try:
            await db.execute("ALTER TABLE jobs ADD COLUMN profile_id INTEGER DEFAULT 1")
            await db.execute("UPDATE jobs SET profile_id = 1 WHERE profile_id IS NULL")
            logger.info("Migración: columna profile_id agregada a jobs.")
        except Exception:
            pass  # Ya existe

        # Tabla de logs de escaneo
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER DEFAULT 1,
                scan_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                site TEXT,
                jobs_found INTEGER DEFAULT 0,
                jobs_applied INTEGER DEFAULT 0,
                errors TEXT
            )
        """)
        try:
            await db.execute("ALTER TABLE scan_log ADD COLUMN profile_id INTEGER DEFAULT 1")
        except Exception:
            pass

        # Tabla de configuración de sitios habilitados (global)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS site_configs (
                site_name TEXT PRIMARY KEY,
                is_enabled BOOLEAN DEFAULT 1
            )
        """)
        
        # Poblar con sitios por defecto si está vacía
        default_sites = [
            'tecoloco', 'computrabajo', 'opcionempleo',
            'acciontrabajo', 'encuentra24', 'linkedin',
            # Nuevos portales (stubs — postulación pendiente de entrenamiento)
            'magneto', 'bumeran', 'olx'
        ]
        for site in default_sites:
            await db.execute("""
                INSERT OR IGNORE INTO site_configs (site_name, is_enabled) 
                VALUES (?, 0)
            """, (site,))
        # Los portales originales activos por defecto
        for site in ['tecoloco', 'computrabajo', 'opcionempleo', 'acciontrabajo', 'encuentra24', 'linkedin']:
            await db.execute("""
                INSERT OR IGNORE INTO site_configs (site_name, is_enabled) 
                VALUES (?, 1)
            """, (site,))
        
        # Tabla de configuración de aplicaciones (Salario, etc.) - legacy
        await db.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # Poblar con valores por defecto
        default_settings = [
            ('tecoloco_salary', '12000'),
            ('tecoloco_working', 'No')
        ]
        for key, value in default_settings:
            await db.execute("""
                INSERT OR IGNORE INTO app_settings (key, value) 
                VALUES (?, ?)
            """, (key, value))
        
        # Tabla de perfiles de candidatos
        await db.execute("""
            CREATE TABLE IF NOT EXISTS candidate_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                is_active INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                location TEXT,
                birth_date TEXT,
                civil_status TEXT,
                address TEXT,
                education TEXT,
                experience TEXT,
                skills TEXT,
                languages TEXT,
                salary_expectation TEXT,
                availability TEXT,
                about TEXT,
                applied_sites TEXT DEFAULT 'all',
                created_at TEXT DEFAULT (datetime('now', '-6 hours'))
            )
        """)

        # Migración: eliminar notification_email (limpieza)
        try:
            await db.execute("ALTER TABLE candidate_profiles DROP COLUMN notification_email")
            logger.info("Migración: columna notification_email eliminada.")
        except Exception:
            pass  # Ya eliminada o versión de SQLite antigua

        # Semilla: perfil de Katty Valentina Coleman (id=1)
        await db.execute("""
            INSERT OR IGNORE INTO candidate_profiles
            (id, name, email, phone, location, birth_date, civil_status, address,
             education, experience, skills, languages, salary_expectation, availability, about,
             applied_sites)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'all')
        """, (
            "Katty Valentina Coleman Antonio",
            "kmolly220@gmail.com",
            "8667-6024",
            "Managua, Nicaragua",
            "07 de mayo de 2005",
            "Soltera",
            "Resd. Jardines de Veracruz, Casa G22, Managua",
            "Licenciatura en Marketing, Universidad Central de Nicaragua (2022-Presente, en curso); "
            "Bachillerato en Ciencias y Letras, Colegio Buenas Orientaciones (2019-2021)",
            "Mesera / Atención al Cliente en Unic Grill & Chill (sep-oct 2025): atención directa, "
            "manejo de múltiples mesas, coordinación con cocina, gestión de caja y pagos. "
            "Mesera / Atención al Cliente en Sabor Persa (nov-dic 2025): servicio en restaurante "
            "especializado, asesoría a clientes sobre menú, resolución de situaciones con profesionalismo.",
            "Microsoft Office (Word, Excel, PowerPoint - nivel intermedio), Atención al cliente presencial, "
            "Manejo de conflictos, Trabajo en equipo, Gestión de múltiples tareas, Adaptabilidad, "
            "Redes sociales, Análisis de mercado básico, Publicidad",
            "Español (nativo)",
            "C$8,000 - C$12,000 mensuales",
            "Inmediata, tiempo completo o medio tiempo",
            "Joven profesional nicaragüense con experiencia en atención al cliente en entornos de ritmo "
            "acelerado. Estudiante activa de Marketing en la Universidad Central de Nicaragua. "
            "Me caracterizo por mi actitud proactiva, puntualidad y capacidad para adaptarme "
            "rápidamente a diferentes ambientes de trabajo.",
            "kmolly220@gmail.com",
        ))

        # ─── Credenciales por perfil y portal ──────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS profile_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                site_name TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                password TEXT NOT NULL DEFAULT '',
                UNIQUE(profile_id, site_name),
                FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE
            )
        """)

        # Semilla: credenciales actuales de Katty (profile_id=1)
        katty_sites = [
            'tecoloco', 'computrabajo', 'opcionempleo',
            'acciontrabajo', 'encuentra24', 'linkedin',
            # Nuevos portales (credenciales a llenar desde el dashboard)
            'magneto', 'bumeran', 'olx'
        ]
        katty_creds = {
            'tecoloco':     ('kmolly220@gmail.com', 'KattyColeman0003'),
            'computrabajo': ('kmolly220@gmail.com', 'KattyColeman003'),
            'opcionempleo': ('kmolly220@gmail.com', 'KattyColeman0003'),
            'acciontrabajo':('kmolly220@gmail.com', 'KattyColeman003'),
            'encuentra24':  ('kmolly220@gmail.com', 'KattyColeman0003'),
            'linkedin':     ('kmolly220@gmail.com', 'KattyColeman003'),
            # Nuevos: credenciales vacías por defecto (llenar desde /profile)
            'magneto':      ('', ''),
            'bumeran':      ('', ''),
            'olx':          ('', ''),
        }
        for site in katty_sites:
            email, pwd = katty_creds.get(site, ('', ''))
            await db.execute("""
                INSERT OR IGNORE INTO profile_credentials (profile_id, site_name, email, password)
                VALUES (1, ?, ?, ?)
            """, (site, email, pwd))

        # Tabla de keywords por candidato
        await db.execute("""
            CREATE TABLE IF NOT EXISTS profile_keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                is_enabled INTEGER DEFAULT 1,
                FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id)
            )
        """)

        # Semilla: keywords de Katty (profile_id=1)
        katty_keywords = [
            "administración", "asistente administrativa", "atención al cliente",
            "marketing", "recepcionista", "oficina", "secretaria", "ventas"
        ]
        for kw in katty_keywords:
            await db.execute("""
                INSERT INTO profile_keywords (profile_id, keyword, is_enabled)
                SELECT 1, ?, 1 WHERE NOT EXISTS (
                    SELECT 1 FROM profile_keywords WHERE profile_id = 1 AND keyword = ?
                )
            """, (kw, kw))

        # Tabla de cookies de sesión (por perfil + sitio)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS session_cookies (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER DEFAULT 1,
                site       TEXT NOT NULL,
                cookies    TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now', '-6 hours')),
                note       TEXT,
                UNIQUE(profile_id, site)
            )
        """)
        # Migración: agregar profile_id a session_cookies existentes
        try:
            await db.execute("ALTER TABLE session_cookies ADD COLUMN profile_id INTEGER DEFAULT 1")
            # Reconstruir UNIQUE si es necesario (SQLite no permite ALTER UNIQUE, ignorar)
        except Exception:
            pass

        # ── Departamentos de Nicaragua ──────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS nicaragua_departments (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)
        nicaragua_depts = [
            "Boaco", "Carazo", "Chinandega", "Chontales", "Estelí",
            "Granada", "Jinotega", "León", "Madriz", "Managua",
            "Masaya", "Matagalpa", "Nueva Segovia", "Río San Juan",
            "Rivas", "RAAN", "RAAS"
        ]
        for dept in nicaragua_depts:
            await db.execute(
                "INSERT OR IGNORE INTO nicaragua_departments (name) VALUES (?)", (dept,)
            )

        # ── Departamentos habilitados por perfil ───────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS profile_departments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id    INTEGER NOT NULL,
                department    TEXT NOT NULL,
                is_enabled    INTEGER DEFAULT 1,
                UNIQUE(profile_id, department),
                FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id)
            )
        """)
        # Semilla: Katty sólo postula en Managua por defecto
        for dept in nicaragua_depts:
            enabled = 1 if dept == "Managua" else 0
            await db.execute("""
                INSERT INTO profile_departments (profile_id, department, is_enabled)
                SELECT 1, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM profile_departments WHERE profile_id = 1 AND department = ?
                )
            """, (dept, enabled, dept))

        await db.commit()
    return True

if __name__ == "__main__":
    # Script para inicializar manualmente si es necesario
    logging.basicConfig(level=logging.INFO)
    asyncio.run(init_db())
    print("Base de datos inicializada correctamente.")
