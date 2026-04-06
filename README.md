# 🤖 PostulacionBot — Sistema Automatizado de Búsqueda y Postulación de Empleos

Sistema inteligente de automatización de búsqueda y postulación de ofertas laborales en portales de Nicaragua. Diseñado para correr 24/7 en un VPS de DigitalOcean con Ubuntu, gestionado con PM2, y con despliegue continuo via GitHub Actions.

> **VPS:** `198.211.97.243:8765`  
> **Repositorio:** `https://github.com/MiguelGotea/PostulacionBotVPS`

---

## ⚠️ Importante: Entorno Virtual (VENV)

Para ejecutar cualquier script de Python (`db.py`, `main.py`, etc.) o usar bibliotecas como `playwright` manualmente en el VPS, **siempre** debes activar el entorno virtual primero:

```bash
cd /root/postulacion-bot
source venv/bin/activate  # o source env/bin/activate
```

Si no ves el prefijo `(venv)` o `(env)` en tu terminal, los comandos fallarán porque las dependencias no estarán disponibles globalmente.

---

## 🗂️ Estructura del Proyecto

```
PostulacionBotVPS/
├── main.py                  # Punto de entrada del sistema
├── scheduler.py             # Orquestador de ciclos de escaneo y postulación
├── db.py                    # Inicialización y esquema de la base de datos SQLite
├── config.py                # Credenciales, keywords, parámetros (NO se sube a GitHub)
├── requirements.txt         # Dependencias Python
├── ecosystem.config.js      # Configuración de PM2
├── gitpush.ps1              # Script PowerShell de despliegue rápido (Windows)
│
├── scrapers/                # Módulos de búsqueda de empleos (Playwright)
│   ├── base.py              # Clase base con lógica compartida de scraping
│   ├── tecoloco.py          # Scraper de Tecoloco.com.ni
│   ├── computrabajo.py      # Scraper de Computrabajo.com.ni
│   ├── opcionempleo.py      # Scraper de OpcionEmpleo.com.ni
│   ├── acciontrabajo.py     # Scraper de AccionTrabajo.com
│   ├── encuentra24.py       # Scraper de Encuentra24.com
│   └── linkedin.py          # Scraper de LinkedIn
│
├── poster/                  # Módulos de postulación automática (Playwright)
│   ├── base.py              # Clase base con login, apply y mark_applied
│   ├── tecoloco.py          # Flujo completo: Login orgánico + Cuestionario
│   ├── computrabajo.py      # Postulación en Computrabajo
│   ├── opcionempleo.py      # Postulación en OpcionEmpleo
│   └── acciontrabajo.py     # Postulación en Acciontrabajo
│
├── dashboard/               # Interfaz Web (FastAPI + Jinja2)
│   ├── app.py               # Rutas del Dashboard
│   ├── templates/           # Plantillas HTML (Nuevas Ofertas, Postuladas, Config)
│   └── static/              # CSS, JS del Dashboard
│
├── data/
│   └── jobs.db              # Base de datos SQLite (No en GitHub)
│
└── logs/                    # Logs de PM2 (No en GitHub)
```

---

## ✨ Funcionalidades

### 🔍 Búsqueda Automática
- Escanea múltiples portales de empleo en Nicaragua cada **1 hora**.
- Filtra por keywords configurables: `administración`, `atención al cliente`, `ventas`, etc.
- Solo guarda **ofertas individuales** con ID numérico. Ignora categorías y listas.
- Detecta y evita duplicar ofertas ya guardadas.

### 🤖 Postulación Automática
- Flujo de **Navegación Orgánica**: entra primero a la oferta y deja que el sitio pida login, evitando detección de bots.
- Soporte completo del **Cuestionario de Tecoloco**: responde automáticamente salario esperado y estado laboral.
- Camuflaje activo: **User-Agent real + Viewport aleatorio** para parecer un navegador humano.
- Supera la protección **Akamai Bot Manager** de Tecoloco con paciencia adaptativa (hasta 90 segundos de espera en la "sala de espera" de Akamai).

### 📊 Dashboard Web (`http://IP:8765`)
- **Nuevas Ofertas**: Lista de empleos encontrados con filtro por portal.
- **Postuladas**: Historial de postulaciones con estado (`applied` / `failed`) y mensaje de error detallado.
- **Configuración**:
  - Habilitar/deshabilitar cada portal individualmente.
  - **Escanear ahora**: Disparar un escaneo manual de un portal específico sin interrumpir el ciclo automático.
  - **Parámetros de Tecoloco**: Configurar expectativa salarial y estado laboral desde el Dashboard.
  - **Limpiar Ofertas Inválidas**: Purga links de categorías que el bot pudo haber guardado por error.
- Todas las fechas se muestran en **hora de Managua (UTC-6)**.


---

## ⚙️ Configuración (`config.py`)

> ⚠️ **Este archivo NUNCA se sube a GitHub.** Está en `.gitignore`.

```python
KEYWORDS = ["administración", "atención al cliente", "ventas", ...]
LOCATION = "Managua, Nicaragua"

CREDENTIALS = {
    "tecoloco":     {"email": "...", "password": "..."},
    "computrabajo": {"email": "...", "password": "..."},
    "opcionempleo": {"email": "...", "password": "..."},
    # ...
}

DASHBOARD_PORT = 8765
SCAN_INTERVAL_HOURS = 1
MAX_APPLICATIONS_PER_RUN = 10
```

---

## 🗄️ Base de Datos (`data/jobs.db`)

El sistema usa **SQLite** con 4 tablas:

| Tabla | Descripción |
|-------|-------------|
| `jobs` | Todas las ofertas encontradas, su estado (`new`, `applied`, `failed`) y errores |
| `scan_log` | Registro de cada ejecución de scraper (cuántas encontró, errores) |
| `site_configs` | Estado activo/inactivo de cada portal (se edita desde el Dashboard) |
| `app_settings` | Parámetros configurables: salario esperado (`tecoloco_salary`), estado laboral (`tecoloco_working`) |

---

## 🚀 Instalación desde Cero (VPS Ubuntu)

```bash
# 1. Clonar el repositorio
git clone https://github.com/MiguelGotea/PostulacionBotVPS.git /root/postulacion-bot
cd /root/postulacion-bot

# 2. Crear entorno virtual e instalar dependencias
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Instalar navegadores de Playwright
playwright install chromium
playwright install-deps chromium

# 4. Crear config.py con tus datos reales (NO está en el repo)
nano config.py

# 5. Inicializar la base de datos
python3 db.py

# 6. Arrancar con PM2
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

---

## 🔄 Despliegue Automático (CI/CD)

El proyecto usa **GitHub Actions** (`.github/workflows/deploy.yml`) para despliegue continuo:

1. **Desde Windows:** Ejecutar `.\gitpush.ps1` en PowerShell.
2. El script hace commit, push y sincroniza con GitHub.
3. GitHub Actions conecta al VPS por SSH y ejecuta `git pull` + `pm2 restart`.

```
[Tu PC] → gitpush.ps1 → GitHub → Actions → VPS (SSH) → pm2 restart
```

**Secretos requeridos en GitHub** (`Settings > Secrets`):
- `VPS_HOST`: IP del servidor DigitalOcean.
- `VPS_USER`: `root`.
- `SSH_PRIVATE_KEY`: Llave SSH privada para conectarse al VPS.

---

## 🖥️ Comandos Útiles en el VPS

```bash
# Ver estado del bot
pm2 status

# Ver logs en tiempo real
pm2 logs postulacion-bot

# Reiniciar después de cambios manuales
pm2 restart postulacion-bot

# Ver el dashboard desde el servidor
curl http://localhost:8765
```

---

## 🔐 Seguridad

- `config.py` está en `.gitignore` y **NUNCA se sube a GitHub**.
- El Dashboard no tiene autenticación (solo accesible desde la IP del VPS).

---

## 📈 Portales Soportados

| Portal | Scraping | Postulación | Notas |
|--------|----------|-------------|-------|
| Tecoloco.com.ni | ✅ | ✅ | Flujo orgánico + Cuestionario automático con Akamai bypass |
| Computrabajo.com.ni | ✅ | ✅ | |
| OpcionEmpleo.com.ni | ✅ | ✅ | Búsqueda por URL directa |
| AccionTrabajo.com | ✅ | ✅ | |
| Encuentra24.com | ✅ | ⏳ | Solo scraping activo |
| LinkedIn | ✅ | ⏳ | Solo scraping activo |

---

## 🗺️ Roadmap

- [ ] Autenticación básica en el Dashboard
- [ ] Soporte para más portales (Indeed, Glassdoor)
- [ ] Notificaciones por Telegram
- [ ] Filtro por salario mínimo en las búsquedas
