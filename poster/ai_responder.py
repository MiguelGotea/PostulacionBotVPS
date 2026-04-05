"""
poster/ai_responder.py
Módulo de respuesta inteligente con Gemini AI para preguntas de formularios de empleo.

Usa la REST API de Google directamente (igual que pitayabot),
sin SDK de python — más estable y sin problemas de versiones.
Modelo: gemini-flash-latest (alias siempre actualizado)
"""
import json
import logging
import asyncio
import re
import aiohttp
from config import API_KEY_GEMINI

logger = logging.getLogger(__name__)

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-flash-latest:generateContent?key={api_key}"
)


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
    Llama a Gemini AI (REST directa, igual que pitayabot) para responder preguntas.

    Args:
        questions: [{"id": "Answer123", "text": "¿Cuál es tu aspiración salarial?"}]
        profile: dict con los datos del candidato desde candidate_profiles

    Returns:
        {"Answer123": "Respuesta generada", ...}
    """
    if not questions:
        return {}

    prompt = _build_prompt(questions, profile)
    url = GEMINI_URL.format(api_key=API_KEY_GEMINI)

    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 1500
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"[AI] Gemini HTTP {resp.status}: {body[:300]}")
                    return _fallback_answers(questions, profile)

                data = await resp.json()

        raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        logger.info(f"[AI] Gemini respondió. Texto raw: {raw_text[:150]}...")

        # Extraer JSON de la respuesta (puede venir en bloque ```json ... ```)
        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if not json_match:
            logger.error(f"[AI] No se encontró JSON en la respuesta de Gemini")
            return _fallback_answers(questions, profile)

        answers = json.loads(json_match.group())
        logger.info(f"[AI] {len(answers)} respuestas generadas correctamente")
        return answers

    except json.JSONDecodeError as e:
        logger.error(f"[AI] Error parseando JSON de Gemini: {e}")
        return _fallback_answers(questions, profile)
    except Exception as e:
        logger.error(f"[AI] Error llamando a Gemini REST API: {e}")
        return _fallback_answers(questions, profile)


def _fallback_answers(questions: list[dict], profile: dict) -> dict[str, str]:
    """Respuestas genéricas si Gemini falla, para no bloquear la postulación."""
    salary = profile.get('salary_expectation', 'C$10,000 mensuales')
    fallbacks = {
        'salario':        f"Mi expectativa salarial es de {salary}.",
        'aspiraci':       f"Mi aspiración salarial es de {salary}.",
        'experiencia':    "Cuento con experiencia en atención al cliente en entornos de ritmo acelerado, manejo de clientes y coordinación de servicio.",
        'disponibilidad': "Sí, tengo disponibilidad inmediata y flexible de horario.",
        'horario':        "Sí, tengo disponibilidad para adaptarme al horario requerido.",
        'caja':           "Sí, tengo experiencia en manejo de caja durante mi trabajo en restaurantes, incluyendo pagos en efectivo y tarjeta.",
        'atenci':         "Sí, tengo experiencia directa en atención al cliente, habiendo trabajado en dos establecimientos gastronómicos.",
        'default':        "Sí, estoy interesada y cuento con las habilidades necesarias para desempeñarme eficientemente en esta posición."
    }

    results = {}
    for q in questions:
        q_lower = q.get('text', '').lower()
        answer = fallbacks['default']
        for key, resp in fallbacks.items():
            if key != 'default' and key in q_lower:
                answer = resp
                break
        if q.get('id'):
            results[q['id']] = answer

    logger.warning(f"[AI] Usando respuestas fallback para {len(results)} preguntas")
    return results
