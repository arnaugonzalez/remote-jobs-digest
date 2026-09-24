"""Seam 3 (design/CODEBASE-DESIGN.md §2): el único sitio que enumera qué
campos existen. `loader` (Fase 1c) y `wizard` (Fase 2) son dos Adapters sobre
esta misma tabla — ninguno mantiene su propia lista de campos. Añadir un eje
de búsqueda nuevo es una entrada aquí; el wizard lo pregunta sin tocarse.

Interno a `profile` (design/CODEBASE-DESIGN.md: "no forma parte de la
Interface pública de `profile`, salvo para `wizard`").
"""

from __future__ import annotations

from dataclasses import dataclass

from .defaults import PROFILE_DEFAULTS as _D


@dataclass(frozen=True, slots=True)
class FieldSpec:
    path: str                 # "experience.ideal_min" — ruta punteada
    kind: str                 # "int" | "bool" | "str" | "list[str]" | "choice"
    default: object
    help: str                 # una línea, para config.example.yaml y --check
    question: str              # lo que pregunta el wizard
    choices: tuple[str, ...] = ()   # solo si kind == "choice"
    # True solo para listas donde el default ya trae valor útil que perder
    # sería un paso atrás (p.ej. signals.negative trae ~24 señales de
    # protección de base) — la respuesta del wizard se AÑADE, no reemplaza.
    # False (default) para listas que son específicas de una persona
    # (home_hints, accept_modes): ahí SÍ hay que reemplazar el default de
    # otro usuario por el propio, nunca acumularlos.
    merge: bool = False


# El orden importa: es el orden en que el wizard pregunta (Fase 2). País base
# va primero — el prototipo de un agente (prototype/wizard-flow.html) señaló
# que preguntarlo tarde deja huérfanas las respuestas de salario/modalidad
# que ya se dieron.
FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("geo.home_hints", "list[str]", list(_D.geo.home_hints),
             "Where you live (city and/or country): onsite, hybrid and remote there all count.",
             "Where do you live? (city and/or country, comma-separated)"),
    FieldSpec("geo.accept_modes", "list[choice]", sorted(_D.geo.accept_modes),
             "Work modes you accept.",
             "Which work modes do you accept?",
             choices=("remote", "hybrid", "onsite")),
    FieldSpec("stack.weighted", "stack", [c.to_mapping() for c in _D.stack.weighted],
             "Weighted stack table: strong words count anywhere, weak words only in title/tags.",
             "Your core technologies, most specific first (e.g. 'react, next.js, typescript')"),
    FieldSpec("experience.ideal_min", "int", _D.experience.ideal_min,
             "Years of experience: lower end of your ideal band.",
             "Lowest years-of-experience ask that is in your sweet spot?"),
    FieldSpec("experience.ideal_max", "int", _D.experience.ideal_max,
             "Years of experience: upper end of your ideal band.",
             "And the highest?"),
    FieldSpec("experience.stretch", "int", _D.experience.stretch,
             "Years you still consider defensible: goes to REVIEW, not rejected.",
             "Up to how many years asked would you still look at it (as REVIEW, not MATCH)?"),
    FieldSpec("experience.max_required", "int", _D.experience.max_required,
             "Above this many years asked, the job is rejected.",
             "Above how many years asked should a job be rejected?"),
    FieldSpec("company.kind", "choice", _D.company.kind,
             "Kind of company you want.",
             "Product companies only, any, or consultancies too?",
             choices=("product", "any", "consultancy")),
    FieldSpec("salary.floor", "map[str,int]", dict(_D.salary.floor),
             "Salary floor per currency: below it the job is rejected even if everything else fits.",
             "Minimum gross yearly salary? (add the currency: EUR/USD/GBP)"),
    FieldSpec("stack.required", "list[str]", list(_D.stack.required),
             "Words that MUST appear (hard AND). Empty = no filter.",
             "Any technology/word the job must mention? (optional, comma-separated)",
             merge=True),
    FieldSpec("stack.min_score", "int", _D.stack.min_score,
             "Minimum stack score (0-10) to be a MATCH.",
             "How strict on stack fit? (0 = lenient, 10 = exact)"),
    FieldSpec("signals.negative", "list[str]", list(_D.signals.negative),
             "Phrases that reject a job when present. ADDED to the ~24 built-in "
             "ones (US-only, security clearance...), never replacing them.",
             "Any extra phrase that should reject a job automatically? "
             "(optional, comma-separated)",
             merge=True),
    FieldSpec("narrative", "text", "",
             "Free-text description of you, read by the optional LLM judge.",
             "Describe yourself professionally in a sentence or two (optional, Enter to skip)"),
)


def by_path(path: str) -> FieldSpec:
    for f in FIELDS:
        if f.path == path:
            return f
    raise KeyError(f"field not declared in schema: {path!r}")
