#!/usr/bin/env python3
"""Responde preguntas abiertas de formularios de candidatura, en tu voz.

Los ATS meten preguntas de ensayo que no se resuelven con una plantilla:

    "Describe a specific project you're proud of where you had impact across
     the stack. What made it meaningful or challenging?"
    "Tell us about a moment that sparked your interest in healthcare."

Este módulo las contesta usando `research/profile.md` — que incluye las
historias con cifras reales— y adaptándolas a la empresa concreta.

Uso:
    ./rjs answer "Describe a project you're proud of..."
    ./rjs answer "..." --company Alan --context "healthtech, seguros de salud"
    ./rjs answer --file preguntas.txt        # una por línea

También se usa desde apply_kit.py para rellenar las preguntas abiertas que
Greenhouse expone por API.
"""

from __future__ import annotations

from remote_jobs_digest import paths

import argparse
import os
import re
import sys

from remote_jobs_digest import config
from remote_jobs_digest import ai_filter

PROFILE_PATH = str(paths.narrative_file())

# Demográficas/EEO y datos personales que un LLM no debe tocar: o son
# opcionales-protegidas (género, etnia, discapacidad, veteranía) o son hechos
# personales que no están en el perfil (notas del instituto). Se dejan
# _(pendiente)_ para que las conteste el usuario.
DO_NOT_ANSWER = [
    "gender", "género", "ethnicity", "ethnic", "race", "veteran",
    "disability", "disabilities", "sexual orientation", "lgbt",
    "religion", "age ", "date of birth", "high school", "instituto",
]

# Una pregunta es "abierta" si pide narrar algo, no un dato.
OPEN_QUESTION_HINTS = [
    "describe", "tell us", "tell me", "why do you", "what made", "what draws",
    "share a", "give an example", "walk us through", "explain how",
    "what excites", "what interests", "motivat", "proud of", "challeng",
    "experience where", "a time when", "cuéntanos", "describe un",
    "por qué", "qué te", "explica",
]

SYSTEM = """Redactas respuestas a preguntas abiertas de formularios de \
candidatura, EN PRIMERA PERSONA y con la voz del candidato.

CÓMO ESCRIBE ESTE CANDIDATO (imítalo — es su voz real):
- **Abre por el problema técnico o la situación concreta**, nunca por una \
declaración de intenciones ("siempre me ha apasionado...").
- **Cada afirmación lleva una cifra o una decisión concreta.** No "mejoré el \
rendimiento": "p50 de 4,2 s a 2,8 s". No "reduje costes": "de ~800 $/mes a \
~40 $/mes".
- **Estructura implícita problema → hallazgo → acción → resultado.** No pongas \
esos rótulos, pero que se note el hilo.
- **Cierra con la conclusión que extrajo**, formulada como una idea propia: \
"en un producto de IA el coste de inferencia es COGS, no una línea de \
infraestructura"; "fallar rápido y ruidosamente es una feature, no un bug".
- **Detalle específico, no categoría**: no "usé colas de tareas", sino \
"Celery con acks_late=True para durabilidad".
- Cero autobombo. No se califica a sí mismo: describe lo que hizo.

REGLAS DURAS:
- SOLO hechos del perfil que te paso. **No inventes** proyectos, empresas, \
tecnologías, cifras ni anécdotas personales. Si la pregunta pide una \
motivación personal que el perfil no respalda, constrúyela SOLO sobre \
proyectos y experiencias que sí aparezcan, sin fabricar historia vital.
- Escribe en el MISMO idioma de la pregunta.
- 130-200 palabras salvo que la pregunta pida otra cosa.
- Prohibido: "estoy emocionado", "I'm excited", "me apasiona", "I'm \
passionate about", "candidato ideal", "valiosa contribución", "always been \
fascinated".
- Nada de preámbulos ni cierres de cortesía. Solo la respuesta.
- El cierre debe ser una idea CONCRETA y discutible, no una perogrullada. \
"considerar cuidadosamente la arquitectura" o "resolver desafíos complejos" no \
dicen nada: bórralos. Cierra con algo que solo diría alguien que construyó eso.
- No repitas la pregunta ni digas "este proyecto me enorgullece": ya se sabe.
- Precisión técnica: Groq es un proveedor de inferencia, no un framework; \
Whisper es el modelo. Si no estás seguro de cómo encaja una pieza, no la \
menciones.

Devuelve SOLO el texto de la respuesta."""


def read_profile() -> str:
    if not os.path.exists(PROFILE_PATH):
        sys.exit(f"Falta {PROFILE_PATH}")
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return f.read()


def is_open_question(label: str) -> bool:
    """¿Pide narrar algo, o es un dato/consentimiento?"""
    low = re.sub(r"\s+", " ", label.replace("\xa0", " ")).lower()
    if len(low) < 25:
        return False
    if any(b in low for b in DO_NOT_ANSWER):
        return False
    return any(h in low for h in OPEN_QUESTION_HINTS) or low.rstrip().endswith("?")


_ES_HINTS = (" que ", " para ", " con ", " los ", " las ", " una ", " del ",
             "¿", "cuéntanos", "describe un")


def detect_language(text: str) -> str:
    """Idioma de la pregunta. El system prompt está en español, así que sin
    forzarlo explícitamente el modelo responde en español a preguntas en
    inglés."""
    low = f" {text.lower()} "
    return "ESPAÑOL" if sum(low.count(h) for h in _ES_HINTS) >= 2 else "INGLÉS"


def answer_question(key: str, profile: str, question: str,
                    company: str = "", context: str = "",
                    job_desc: str = "") -> str:
    about = [f"La pregunta está en {detect_language(question)}. "
             f"RESPONDE ENTERAMENTE EN {detect_language(question)}."]
    if company:
        about.append(f"Empresa: {company}")
    if context:
        about.append(f"Contexto de la empresa: {context}")
    if job_desc:
        about.append(f"Extracto de la oferta:\n{job_desc[:1500]}")
    body = {
        "model": config.AI_MODEL,
        "temperature": 0.45,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content":
                f"PERFIL DEL CANDIDATO:\n{profile}\n\n"
                + ("\n".join(about) + "\n\n" if about else "")
                + f"---\nPREGUNTA DEL FORMULARIO:\n{question}"},
        ],
    }
    return (ai_filter.post_for_writing(key, body) or "").strip()


def main() -> None:
    p = argparse.ArgumentParser(
        description="Responde preguntas abiertas de formularios en tu voz")
    p.add_argument("question", nargs="*", help="la pregunta, entre comillas")
    p.add_argument("--file", help="fichero con una pregunta por línea")
    p.add_argument("--company", default="", help="empresa, para adaptar")
    p.add_argument("--context", default="",
                   help="a qué se dedica (p.ej. 'healthtech, seguros')")
    args = p.parse_args()

    questions: list[str] = []
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            questions = [l.strip() for l in f if l.strip()]
    if args.question:
        questions.append(" ".join(args.question))
    if not questions:
        p.error("pasa una pregunta entre comillas o usa --file")

    key = ai_filter._resolve_key()
    if not key and not ai_filter._gemini_key():
        sys.exit("Sin clave LLM: define GROQ_API_KEY o GEMINI_API_KEY (.env)")
    profile = read_profile()

    for q in questions:
        if len(questions) > 1:
            print(f"\n{'=' * 70}\n{q}\n{'=' * 70}")
        ans = answer_question(key, profile, q, args.company, args.context)
        print(f"\n{ans}\n")
    if questions:
        print("— Revisa antes de pegar: cambia lo que no dirías tú.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
