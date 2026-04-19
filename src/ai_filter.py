"""
ai_filter.py
────────────
Filtro de relevancia con IA (Gemini via GEMINI_MODEL configurado en config.py).

Antes de guardar un job en la DB, se evalúa si el puesto
es compatible con el perfil del candidato (skills, experiencia,
industria). Reduce postulaciones a empleos irrelevantes.
"""

import json
import logging
import asyncio
import aiosqlite
from config import DB_PATH, GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

# ── Cliente Gemini ────────────────────────────────────────────────────────────
try:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    _model = genai.GenerativeModel(GEMINI_MODEL)
    AI_AVAILABLE = True
except Exception as e:
    logger.warning(f"[AI Filter] Gemini no disponible: {e}")
    AI_AVAILABLE = False

async def _get_profile_summary(profile_id: int) -> str:
    """Carga un resumen del perfil del candidato desde la DB para el prompt."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT name, about, skills, experience, education, salary_expectation FROM candidate_profiles WHERE id = ?",
                (profile_id,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return ""

            async with db.execute(
                "SELECT keyword FROM profile_keywords WHERE profile_id = ? AND is_enabled = 1",
                (profile_id,)
            ) as cur:
                kws = [r[0] for r in await cur.fetchall()]

        return (
            f"Candidato: {row['name']}\n"
            f"Keywords buscadas: {', '.join(kws)}\n"
            f"Skills: {row['skills'] or 'N/A'}\n"
            f"Experiencia: {(row['experience'] or 'N/A')[:300]}\n"
            f"Educación: {(row['education'] or 'N/A')[:200]}\n"
            f"Salario esperado: {row['salary_expectation'] or 'N/A'}\n"
            f"Sobre mí: {(row['about'] or 'N/A')[:300]}"
        )
    except Exception as e:
        logger.error(f"[AI Filter] Error cargando perfil {profile_id}: {e}")
        return ""


async def is_job_relevant(
    job: dict,
    profile_id: int,
    profile_summary: str = None
) -> tuple[bool, str]:
    """
    Evalúa con Gemini si un job es relevante para el candidato.
    
    Returns:
        (is_relevant: bool, reason: str)
    
    Si Gemini no está disponible o falla, retorna (True, "sin filtro")
    para no bloquear el guardado.
    """
    if not AI_AVAILABLE:
        return True, "AI no disponible — guardando sin filtro"

    if not profile_summary:
        profile_summary = await _get_profile_summary(profile_id)

    if not profile_summary:
        return True, "Perfil no encontrado — guardando sin filtro"

    title       = job.get("title", "")
    company     = job.get("company", "")
    description = (job.get("description", "") or "")[:600]

    prompt = f"""Eres un evaluador de compatibilidad laboral. Analiza si este puesto de trabajo es relevante para el candidato.

PERFIL DEL CANDIDATO:
{profile_summary}

OFERTA DE EMPLEO:
Puesto: {title}
Empresa: {company}
Descripción: {description}

INSTRUCCIONES:
- Responde SOLO con JSON válido, sin texto extra.
- Evalúa si el candidato tiene probabilidad razonable de ser considerado.
- Sé flexible: un 30% de match ya es suficiente para intentar.
- Rechaza SOLO si el puesto requiere especialización completamente ajena (ej: médico, abogado, ingeniero de software para alguien de administración).

Responde con:
{{"relevant": true/false, "reason": "explicación breve en español (máx 80 chars)"}}"""

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: _model.generate_content(prompt)
        )
        text = response.text.strip()
        # Limpiar posibles bloques de código markdown
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        
        data = json.loads(text)
        relevant = bool(data.get("relevant", True))
        reason   = data.get("reason", "")
        logger.info(f"[AI Filter] '{title}' → {'✅' if relevant else '❌'} {reason}")
        return relevant, reason

    except json.JSONDecodeError as e:
        logger.warning(f"[AI Filter] JSON inválido de Gemini: {e} — guardando sin filtro")
        return True, "Error parsing AI — guardando sin filtro"
    except Exception as e:
        logger.warning(f"[AI Filter] Error Gemini: {e} — guardando sin filtro")
        return True, f"Error AI: {str(e)[:60]}"


async def filter_jobs_batch(
    jobs: list[dict],
    profile_id: int,
    enabled: bool = True
) -> tuple[list[dict], list[dict]]:
    """
    Filtra una lista de jobs por relevancia IA.
    
    Returns:
        (jobs_aprobados, jobs_rechazados)
    """
    if not enabled or not AI_AVAILABLE:
        return jobs, []

    profile_summary = await _get_profile_summary(profile_id)
    approved, rejected = [], []

    for job in jobs:
        relevant, reason = await is_job_relevant(job, profile_id, profile_summary)
        if relevant:
            approved.append(job)
        else:
            rejected.append({**job, "_ai_reject_reason": reason})
        # Pequeña pausa para no saturar la API
        await asyncio.sleep(0.5)

    logger.info(
        f"[AI Filter] Perfil {profile_id}: {len(approved)} aprobados, "
        f"{len(rejected)} rechazados de {len(jobs)} totales"
    )
    return approved, rejected
