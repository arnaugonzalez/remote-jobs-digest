"""Adapter de deprecación (design/CODEBASE-DESIGN.md §5 D1, §6 fase 1c).

Hasta la Fase 6 del plan, este módulo sigue siendo lo que los 19
consumidores existentes (`import config`) esperan: un conjunto de
constantes con estos mismos nombres. Lo que cambia es DE DÓNDE salen —
antes eran literales escritos aquí a mano; ahora se derivan de
`profile.load_profile()`, que lee `config.local.yaml` (o los defaults del
perfil actual si no existe) y `research/profile.md`.

No añadas una constante nueva aquí para un criterio de búsqueda — eso vive
en `profile/types.py` + `profile/defaults.py`. Este fichero solo debe
crecer para variables de ENTORNO/OPERACIÓN (rate limits, paginación,
timeouts) que deliberadamente NO forman parte de `Profile`
(design/CODEBASE-DESIGN.md §1, "No-seam deliberado").
"""

from __future__ import annotations

import os

from remote_jobs_digest import paths
from remote_jobs_digest.profile import lexicon
from remote_jobs_digest.profile.loader import load_profile

_profile = load_profile()
PROFILE = _profile

# ---------------------------------------------------------------------------
# Fuentes activas para el cron diario (sin login).
# ---------------------------------------------------------------------------
# 'linkedin' va PRIMERA a propósito: el dedup entre fuentes se queda con la
# primera aparición, y ante el mismo puesto en dos sitios conviene quedarse la
# URL de LinkedIn — Easy Apply es donde mejor tasa de respuesta hay.
# 'indeed' usa python-jobspy (es el board grande más scrapeable ahora mismo).
# 'glassdoor'/'google' están registradas pero fuera del cron: Glassdoor
# bloquea a menudo y Google Jobs solapa con los boards ATS. Se pueden probar
# con --sources glassdoor,google.
# 'ats' consulta los boards de cada empresa (Greenhouse/Lever/Ashby/...) usando
# research/data/companies.json. Es la fuente de mayor señal: va a la fuente.
ACTIVE_SOURCES = list(_profile.search.active_sources)

# ---------------------------------------------------------------------------
# LinkedIn (endpoint guest, sources/linkedin.py). Entorno/operación — no
# depende del perfil de búsqueda, depende de cuánto rate-limit aguantas.
# ---------------------------------------------------------------------------
LINKEDIN_SEARCH_TERMS = list(_profile.search.board_queries
                             or _profile.search.search_terms)
LINKEDIN_LOCATIONS = list(_profile.search.board_locations) or ["Worldwide"]
LINKEDIN_HOURS = int(os.getenv("RJS_LINKEDIN_HOURS", "48"))
LINKEDIN_PAGES = int(os.getenv("RJS_LINKEDIN_PAGES", "3"))
LINKEDIN_DELAY = float(os.getenv("RJS_LINKEDIN_DELAY", "2.0"))
LINKEDIN_DESC_MAX = int(os.getenv("RJS_LINKEDIN_DESC_MAX", "60"))

# ---------------------------------------------------------------------------
# Boards vía python-jobspy (sources/jobspy_boards.py). Entorno/operación.
# ---------------------------------------------------------------------------
JOBSPY_SEARCH_TERMS = LINKEDIN_SEARCH_TERMS
JOBSPY_COUNTRIES = list(_profile.search.board_locations) or ["USA"]
JOBSPY_HOURS = int(os.getenv("RJS_JOBSPY_HOURS", "48"))
JOBSPY_RESULTS = int(os.getenv("RJS_JOBSPY_RESULTS", "50"))

# ---------------------------------------------------------------------------
# Stack y relevancia — derivados de profile.stack.
# ---------------------------------------------------------------------------
TECH_KEYWORDS = list(_profile.stack.keywords)
HIGH_SIGNAL_KEYWORDS = list(_profile.stack.high_signal)
# (peso, etiqueta, fuertes, débiles) — mismo shape de tupla que consumía
# filters.py original; classifier.py (Fase 1b) ya no la usa, pero otros
# consumidores todavía no migrados sí.
STACK_WEIGHTS = [(c.weight, c.label, list(c.strong), list(c.weak))
                 for c in _profile.stack.weighted]
MIN_STACK_SCORE = _profile.stack.min_score
SEARCH_TERMS = list(_profile.search.search_terms)

# ---------------------------------------------------------------------------
# Filtrado de aptitud internacional — derivados de profile.signals/geo.
# ---------------------------------------------------------------------------
POSITIVE_SIGNALS = list(_profile.signals.positive)
STRONG_INTL_SIGNALS = list(_profile.signals.strong_intl)
NEGATIVE_SIGNALS = list(_profile.signals.negative)

# Código muerto heredado: declarada en el config.py original, sin ningún
# consumidor real (ni filters.py ni ningún otro módulo la importaba). Se
# mantiene por si algo externo la referenciaba, sin modelarla en Profile.
US_ONLY_LOCATION_HINTS = [
    "usa only", "us only", "united states only", "us-only",
    "united states", "usa",
]

# Léxico fijo (profile/lexicon.py) — no varía por usuario, así que no es
# parte de Profile (DOMAIN-MODEL.md ADR-0001).
US_STATE_CODES = list(lexicon.US_STATE_CODES)
US_CITIES = list(lexicon.US_CITIES)

# Los cuatro roles de GeoPolicy (design/CODEBASE-DESIGN.md §5 D2): "España"
# deja de ser estructura y pasa a ser el VALOR de profile.geo.*.
SPAIN_HINTS = list(_profile.geo.home_hints)
EU_REGION_HINTS = list(_profile.geo.region_hints)
EU_COUNTRY_HINTS = list(_profile.geo.away_hints)
EU_LOCATION_HINTS = SPAIN_HINTS + EU_REGION_HINTS + EU_COUNTRY_HINTS
REMOTE_HINTS = list(_profile.geo.remote_hints)
NON_EU_COUNTRY_HINTS = list(_profile.geo.blocked_hints)
GLOBAL_LOCATION_HINTS = list(_profile.geo.global_hints)
REMOTE_BUT_AMERICAS = list(lexicon.REMOTE_BUT_NORTH_AMERICA)

# ---------------------------------------------------------------------------
# Gate de rol — derivado de profile.role.
# ---------------------------------------------------------------------------
ROLE_SIGNALS = list(_profile.role.role_signals)
NON_ROLE_SIGNALS = list(_profile.role.non_role_signals)
STAFFING_PLATFORMS = list(_profile.company.staffing_platforms)

# ---------------------------------------------------------------------------
# Seniority — derivado de profile.role / profile.experience.
# ---------------------------------------------------------------------------
MANAGEMENT_SIGNALS = list(_profile.role.management_signals)
SENIOR_TITLE_SIGNALS = list(_profile.role.senior_title_signals)
OVERQUALIFIED_SIGNALS = MANAGEMENT_SIGNALS + SENIOR_TITLE_SIGNALS
UNDERQUALIFIED_SIGNALS = list(_profile.role.underqualified_signals)
# Código muerto heredado (ver classifier.py::detect_level): siempre vacío en
# el original, "absorbido por OVERQUALIFIED_SIGNALS" según su propio
# comentario — no se migró a RolePolicy porque nunca tuvo efecto.
SENIOR_SIGNALS: list[str] = []
JUNIOR_SIGNALS = list(_profile.role.junior_signals)
JUNIOR_YEARS_RANGE_SIGNALS = list(_profile.role.junior_years_range_signals)
MID_SIGNALS = list(_profile.role.mid_signals)
ALLOWED_LEVELS = set(_profile.role.allowed_levels)

IDEAL_YEARS = (_profile.experience.ideal_min, _profile.experience.ideal_max)
STRETCH_YEARS = _profile.experience.stretch
MAX_YEARS_REQUIRED = _profile.experience.max_required

# ---------------------------------------------------------------------------
# Modelo de empresa — derivado de profile.company.
# ---------------------------------------------------------------------------
PAYWALLED_DOMAINS = list(_profile.company.paywalled_domains)
CONSULTING_SIGNALS = list(_profile.company.consulting_signals)
CONSULTING_COMPANIES = list(_profile.company.consulting_companies)
EXCLUSIVITY_SIGNALS = list(_profile.signals.exclusivity)

# ---------------------------------------------------------------------------
# Filtro IA (opcional, vía Groq). Entorno/operación — no depende del perfil.
# ---------------------------------------------------------------------------
USE_AI_FILTER = os.getenv("RJS_USE_AI", "1") not in ("0", "false", "no")
AI_MODEL = (os.getenv("RJS_LLM_MODEL") or os.getenv("RJS_AI_MODEL")
            or "llama-3.3-70b-versatile")
AI_BATCH_SIZE = int(os.getenv("RJS_AI_BATCH", "6"))
AI_MAX_JOBS = int(os.getenv("RJS_AI_MAX_JOBS", "120"))
AI_DESC_CHARS = 800
AI_BATCH_DELAY = float(os.getenv("RJS_AI_BATCH_DELAY", "1.5"))
AI_MIN_FIT = int(os.getenv("RJS_AI_MIN_FIT", "5"))

# `Profile.thesis()` interpola los umbrales reales (salario, años) sobre la
# parte narrativa (profile.narrative, leída de research/profile.md) — así
# el juez IA y el filtro determinista no pueden discrepar en silencio
# (design/CODEBASE-DESIGN.md §5 D8). OJO, diferencia real con el USER_THESIS
# original: aquel era prosa completa escrita a mano (contexto de carrera,
# preferencias por orden, qué NO se busca...); `narrative` hoy solo trae lo
# que haya en research/profile.md. Si ese fichero no existe o es más breve
# que el USER_THESIS original, el prompt que ve el juez IA es objetivamente
# más pobre — no es una regresión de este Adapter, es la consecuencia
# esperada de unificar dos fuentes de verdad en una; pendiente de que
# research/profile.md tenga el mismo nivel de detalle si se quiere paridad
# completa con ai_filter.py.
USER_THESIS = _profile.thesis()

# ---------------------------------------------------------------------------
# Salario — derivado de profile.salary.
# ---------------------------------------------------------------------------
SALARY_CURRENCIES = {"$": "USD", "USD": "USD", "€": "EUR", "EUR": "EUR",
                     "£": "GBP", "GBP": "GBP"}
MIN_PLAUSIBLE_ANNUAL, MAX_PLAUSIBLE_ANNUAL = _profile.salary.plausible_annual
MIN_SALARY = dict(_profile.salary.floor)
WARN_SALARY = dict(_profile.salary.warn)
MIN_SALARY_JUNIOR = dict(_profile.salary.floor_junior)
WARN_SALARY_JUNIOR = dict(_profile.salary.warn_junior)

# ---------------------------------------------------------------------------
# Salida — entorno/operación.
# ---------------------------------------------------------------------------
OUTPUT_DIR = str(paths.output_dir())
DIGEST_TOP_N = int(os.getenv("RJS_DIGEST_TOP_N", "12"))
DIGEST_VERDICTS = ["APTA", "REVISAR"]

# HTTP — entorno/operación.
HTTP_TIMEOUT = 25.0
HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
}
