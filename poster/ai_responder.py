"""
poster/ai_responder.py
Módulo de respuesta inteligente con Gemini AI para preguntas de formularios de empleo.
"""
import json
import logging
import asyncio
import re
import google.generativeai as genai
from config import API_KEY_GEMINI, GEMINI_MODEL

logger = logging.getLogger(__name__)

# Configurar Gemini una sola vez al importar
genai.configure(api_key=API_KEY_GEMINI)
_model = genai.GenerativeModel(GEMINI_MODEL)


def _build_prompt(questions: list[dict], profile: dict) -> str:
    """Construye el prompt para Gemini con el perfil del candidato y las preguntas."""
    questions_text = "\n".join(
        f'- ID: "{q["id"]}" | Pregunta: "{q["text"]}"'
        for q in questions if q.get("text")
    )

    return f"""Eres {profile.get('name', 'una candidata')}, nicaragüense buscando empleo.

PERFIL:
- Ubicación: {profile.get('location', 'Managua, Nicaragua')}
- Educación: {profile.get('education', 'Bachillerato completo')}
- Experiencia: {profile.get('experience', 'Experiencia en atención al cliente')}
- Habilidades: {profile.get('skills', 'Atención al cliente, comunicación, trabajo en equipo')}
- Expectativa salarial: {profile.get('salary_expectation', 'A convenir')}
- Disponibilidad: {profile.get('availability', 'Inmediata')}
- Descripción personal: {profile.get('about', '')}

INSTRUCCIONES:
Responde las siguientes preguntas de un formulario de trabajo.
- Sé breve (2-4 oraciones máximo por respuesta).
- Usa primera persona, tono natural y positivo.
- Si la pregunta es sobre experiencia que tienes, menciónala. Si no tienes la experiencia exacta, menciona habilidades relacionadas de forma honesta y optimista.
- Para preguntas de disponibilidad: siempre responde que sí tienes disponibilidad.
- Para preguntas de experiencia en caja o pagos: menciona el manejo de caja en los restaurantes.
- Para expectativa salarial: usa "{profile.get('salary_expectation', 'C$8,000 - C$12,000')}".
- Devuelve ÚNICAMENTE un objeto JSON válido con formato {{"id_pregunta": "respuesta"}}.
- No incluyas texto antes ni después del JSON. Solo el JSON.

PREGUNTAS:
{questions_text}

JSON de respuestas:"""


async def answer_questions(questions: list[dict], profile: dict) -> dict[str, str]:
    """
    Llama a Gemini AI para responder las preguntas del formulario.
    
    Args:
        questions: Lista de dicts con {"id": "Answer123", "text": "¿Pregunta?"}
        profile: Dict con los datos del candidato desde candidate_profiles
    
    Returns:
        Dict {"Answer123": "Respuesta generada", ...}
    """
    if not questions:
        return {}

    prompt = _build_prompt(questions, profile)

    try:
        # Llamada async a Gemini
        response = await asyncio.to_thread(_model.generate_content, prompt)
        raw_text = response.text.strip()

        # Extraer JSON de la respuesta (puede venir envuelto en ```json ... ```)
        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if not json_match:
            logger.error(f"[AI] Gemini no devolvió JSON válido: {raw_text[:200]}")
            return _fallback_answers(questions, profile)

        answers = json.loads(json_match.group())
        logger.info(f"[AI] Gemini respondió {len(answers)} preguntas correctamente")
        return answers

    except json.JSONDecodeError as e:
        logger.error(f"[AI] Error parseando JSON de Gemini: {e}")
        return _fallback_answers(questions, profile)
    except Exception as e:
        logger.error(f"[AI] Error llamando a Gemini: {e}")
        return _fallback_answers(questions, profile)


def _fallback_answers(questions: list[dict], profile: dict) -> dict[str, str]:
    """Respuestas genéricas si Gemini falla, para no bloquear la postulación."""
    salary = profile.get('salary_expectation', 'C$10,000 mensuales')
    fallbacks = {
        'salario': f"Mi expectativa salarial es de {salary}.",
        'aspiraci': f"Mi aspiración salarial es de {salary}.",
        'experiencia': "Cuento con experiencia en atención al cliente en entornos de ritmo acelerado.",
        'disponibilidad': "Sí, tengo disponibilidad inmediata y flexible de horario.",
        'horario': "Sí, tengo disponibilidad para adaptarme al horario requerido.",
        'caja': "Sí, tengo experiencia básica en manejo de caja y pagos durante mi trabajo en restaurantes.",
        'default': "Sí, estoy interesada y cuento con las habilidades necesarias para desempeñarme en esta posición."
    }

    results = {}
    for q in questions:
        q_lower = q.get('text', '').lower()
        answer = fallbacks['default']
        for key, resp in fallbacks.items():
            if key in q_lower:
                answer = resp
                break
        if q.get('id'):
            results[q['id']] = answer

    logger.warning(f"[AI] Usando respuestas fallback para {len(results)} preguntas")
    return results
