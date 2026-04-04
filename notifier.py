import logging
import aiosmtplib
from email.message import EmailMessage
from datetime import datetime
from config import EMAIL_CONFIG

logger = logging.getLogger(__name__)

async def send_summary(applied_jobs: list, manual_jobs: list, stats: dict):
    """Envía un resumen por email con las postulaciones y nuevas ofertas manuales."""
    
    if not EMAIL_CONFIG['sender_email'] or not EMAIL_CONFIG['sender_password']:
        logger.warning("Configuración de email incompleta. Saltando envío de resumen.")
        return

    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    
    # Construir el HTML
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #1A5276;">Katty Jobs — Resumen de Actividad ({now})</h2>
        
        <p>Se han realizado <strong>{len(applied_jobs)}</strong> postulaciones automáticas exitosas en este ciclo.</p>
        
        <h3 style="color: #0E6655;">✅ Postulaciones Realizadas</h3>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
            <tr style="background-color: #f2f2f2;">
                <th>Puesto</th>
                <th>Empresa</th>
                <th>Sitio</th>
                <th>Link</th>
            </tr>
    """
    
    for job in applied_jobs:
        color = "#1A5276"
        site = job.get('site', '').lower()
        if 'computrabajo' in site: color = "#0E6655"
        elif 'opcionempleo' in site: color = "#6C3483"
        elif 'acciontrabajo' in site: color = "#784212"
        
        html_content += f"""
            <tr>
                <td>{job.get('title')}</td>
                <td>{job.get('company')}</td>
                <td><span style="color: {color}; font-weight: bold;">{job.get('site')}</span></td>
                <td><a href="{job.get('url')}">Ver Oferta</a></td>
            </tr>
        """
        
    html_content += """
        </table>
        
        <h3 style="color: #E67E22; margin-top: 25px;">⚠️ Ofertas que requieren acción manual</h3>
        <p>Las siguientes ofertas de Encuentra24 o LinkedIn requieren que postules tú mismo:</p>
        <ul>
    """
    
    for job in manual_jobs:
        title = job.get('title') if isinstance(job, dict) else job['title']
        company = job.get('company') if isinstance(job, dict) else job['company']
        site = job.get('site') if isinstance(job, dict) else job['site']
        url = job.get('url') if isinstance(job, dict) else job['url']
        html_content += f"<li><strong>{title}</strong> en {company} ({site}) - <a href='{url}'>Ir a la oferta</a></li>"
        
    html_content += f"""
        </ul>
        
        <hr style="margin-top: 30px;">
        <p style="font-size: 0.9em; color: #777;">
            Estadísticas del ciclo: Encontrados: {stats.get('found', 0)} | Errores: {stats.get('errors', 0)}<br>
            Ver dashboard completo en: <a href="http://{stats.get('vps_ip', 'IP')}:8765">Katty Jobs Dashboard</a>
        </p>
    </body>
    </html>
    """

    # Configurar el mensaje
    msg = EmailMessage()
    msg["Subject"] = f"Katty Jobs — {len(applied_jobs)} postulaciones | {now}"
    msg["From"] = EMAIL_CONFIG['sender_email']
    msg["To"] = EMAIL_CONFIG['recipient_email']
    msg.set_content("Este correo requiere visualización HTML.")
    msg.add_alternative(html_content, subtype="html")

    try:
        await aiosmtplib.send(
            msg,
            hostname=EMAIL_CONFIG['smtp_host'],
            port=EMAIL_CONFIG['smtp_port'],
            username=EMAIL_CONFIG['sender_email'],
            password=EMAIL_CONFIG['sender_password'],
            use_tls=False,
            start_tls=True
        )
        logger.info(f"Resumen enviado correctamente a {EMAIL_CONFIG['recipient_email']}")
    except Exception as e:
        logger.error(f"Error enviando email de resumen: {e}")
