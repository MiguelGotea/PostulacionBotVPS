# Katty Jobs — Sistema de Monitoreo y Postulación de Empleos

Sistema automatizado de búsqueda y postulación de ofertas de trabajo en Nicaragua. Diseñado para correr en un VPS de DigitalOcean con Ubuntu 24.04.

## Estructura del Proyecto

- `scrapers/`: Lógica de extracción de ofertas (Playwright).
- `poster/`: Lógica de postulación automática de ofertas.
- `dashboard/`: Interfaz web (FastAPI + Jinja2) para gestión.
- `data/`: Base de datos SQLite.
- `logs/`: Historial de ejecuciones y errores.

## Requisitos en el Host (VPS)

1. **Python 3.12+**
2. **PM2** (instalado globalmente con npm)
3. **Playwright Chromium**

## Instalación desde Cero

```bash
# 1. Clonar el repositorio
git clone https://github.com/MiguelGotea/PostulacionBotVPS.git
cd PostulacionBotVPS

# 2. Crear entorno virtual e instalar dependencias
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Instalar navegadores de Playwright
playwright install chromium
playwright install-deps chromium
```

## Configuración Obligatoria

1. Edita `config.py` con tus credenciales reales para Tecoloco, Computrabajo, etc.
2. Configura los datos de Gmail (SMTP) para recibir los resúmenes.

## Iniciar el Sistema con PM2

```bash
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

## Comandos Útiles

- `pm2 status`: Ver estado del bot.
- `pm2 logs katty-jobs`: Ver actividad en tiempo real.
- `pm2 restart katty-jobs`: Reiniciar después de cambios.

## Acceso al Dashboard

El dashboard está disponible en `http://TU_IP_VPS:8765`.
Ahí podrás ver las nuevas ofertas, el historial de postulaciones y la configuración de búsqueda.
