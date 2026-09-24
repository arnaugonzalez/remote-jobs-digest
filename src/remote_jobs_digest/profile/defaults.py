"""`Profile.defaults()` — los valores ACTUALES del perfil original, en código (I8).

Fase 1a de docs/OSS-PLAN.md: "comportamiento idéntico verificable". Estos son
exactamente los valores que hoy viven como constantes de módulo en
config.py — están aquí para que, tras la Fase 1c, `import config` siga
produciendo los mismos resultados sin que nadie note el cambio de fuente.

NO son "los defaults recomendados para cualquier usuario" — son el perfil
real de una persona concreta, dejado así a propósito para el golden test de
la Fase 1b (mismos verdicts oferta por oferta que antes del refactor). El
wizard de la Fase 2 es lo que le da a otro usuario SUS propios valores.
"""

from __future__ import annotations

from .types import (
    CompanyKindPolicy, ExperienceBand, GeoPolicy, Profile, RolePolicy,
    SalaryPolicy, SearchPolicy, SignalPolicy, StackCategory, StackPolicy,
)

_EXPERIENCE = ExperienceBand(ideal_min=2, ideal_max=4, stretch=5, max_required=5)

_SALARY = SalaryPolicy(
    floor={"EUR": 40_000, "USD": 60_000, "GBP": 35_000},
    warn={"EUR": 30_000, "USD": 45_000, "GBP": 26_000},
    floor_junior={"EUR": 35_000, "USD": 52_000, "GBP": 30_000},
    warn_junior={"EUR": 26_000, "USD": 39_000, "GBP": 22_000},
    plausible_annual=(20_000, 600_000),
)

_STACK = StackPolicy(
    required=(),
    weighted=(
        StackCategory(3, "backend_python",
            ("fastapi", "django", "flask", "celery", "sqlalchemy", "pydantic",
             "asyncio", "python backend", "backend python"),
            ("python", "backend")),
        StackCategory(3, "ai_llm",
            ("llm", "genai", "generative ai", "langchain", "langgraph", "rag",
             "retrieval augmented", "mlops", "applied ai", "embeddings",
             "pgvector", "vector database", "fine-tuning",
             "prompt engineering", "ai engineer", "llmops", "agentic"),
            ("ai", "machine learning", "ml", "nlp", "openai", "anthropic")),
        StackCategory(3, "infra",
            ("terraform", "opentelemetry", "prometheus", "grafana", "iac",
             "infrastructure as code", "platform engineer", "site reliability"),
            ("aws", "kubernetes", "docker", "devops", "gcp", "azure", "cloud",
             "observability", "infrastructure")),
        StackCategory(3, "typescript_node",
            ("typescript", "node.js", "nodejs", "nestjs", "express.js",
             "next.js", "deno", "trpc"),
            ("node", "javascript", "react", "graphql")),
        StackCategory(1, "mobile",
            ("react native", "flutter", "expo"),
            ("mobile", "kotlin", "android", "ios")),
        StackCategory(1, "regulated",
            ("insurtech", "regtech", "fintech", "regulated environment"),
            ("insurance", "banking", "payments", "compliance", "finance")),
    ),
    min_score=3,
    score_cap=10,
    keywords=(
        "python", "fastapi", "django", "flask", "flutter", "dart",
        "react native", "backend", "full stack", "fullstack", "full-stack",
        "aws", "gcp", "cloud", "devops", "postgres", "postgresql",
        "ai", "a.i.", "llm", "machine learning", "ml engineer", "mlops",
        "genai", "generative ai", "langchain", "langgraph", "rag",
        "n8n", "automation", "integration", "api",
        "node", "typescript", "javascript",
    ),
    high_signal=(
        "python", "fastapi", "django", "flask", "flutter", "dart",
        "react native", "llm", "langchain", "langgraph", "rag",
        "machine learning", "mlops", "genai", "generative ai", "n8n",
        "typescript", "nestjs", "terraform", "kubernetes",
    ),
)

# GeoPolicy: "España" deja de ser estructura y pasa a ser el VALOR de estos
# cuatro roles (CODEBASE-DESIGN.md §5 D2). home=España, region=Europa/EMEA
# (contiene home), away=resto de países UE concretos, blocked=fuera de la UE.
_GEO = GeoPolicy(
    home_hints=(
        "spain", "españa", "barcelona", "madrid", "valencia", "sevilla",
        "bilbao", "zaragoza", "malaga",
    ),
    region_hints=("europe", "european", "emea", "eu ", " eu", "cet", "cest", "eea"),
    away_hints=(
        "uk", "united kingdom", "london", "manchester", "berlin", "germany",
        "munich", "france", "paris", "amsterdam", "netherlands", "portugal",
        "lisbon", "porto", "ireland", "dublin", "poland", "warsaw", "krakow",
        "italy", "milan", "rome", "sweden", "stockholm", "denmark",
        "copenhagen", "zurich", "switzerland", "vienna", "austria",
        "belgium", "brussels", "helsinki", "finland", "norway", "oslo",
        "prague", "czech", "romania", "bucharest", "athens", "greece",
        "budapest", "hungary", "estonia", "tallinn", "lithuania", "latvia",
        "bulgaria", "sofia",
    ),
    blocked_hints=(
        "india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai",
        "brazil", "brasil", "são paulo", "sao paulo", "mexico", "méxico",
        "argentina", "buenos aires", "colombia", "bogota", "chile", "peru",
        "philippines", "manila", "singapore", "japan", "tokyo", "china",
        "shanghai", "hong kong", "australia", "sydney", "melbourne",
        "new zealand", "south africa", "nigeria", "kenya", "egypt", "israel",
        "tel aviv", "turkey", "türkiye", "istanbul", "dubai", "uae", "qatar",
        "doha", "saudi", "korea", "seoul", "vietnam", "indonesia", "thailand",
        "pakistan", "bangladesh", "ukraine", "serbia", "belgrade",
    ),
    # Promesa de alcance mundial EXPLÍCITA -> decide 'ok' directamente
    # (antes GLOBAL_LOCATION_HINTS de config.py). Deliberadamente SIN
    # "latam"/"europe"/"emea"/"americas"/"international": esas son evidencia
    # más débil ("no es solo-EE.UU."), no una afirmación de alcance
    # mundial — van en `non_us_signals`, no aquí. Confundir los dos roles
    # es precisamente el bug corregido en producción el 2026-08-13
    # ("Remote — LATAM" pasando como APTA automática).
    global_hints=(
        "worldwide", "anywhere", "global", "remote - global", "fully remote",
        "work from anywhere", "any location", "location agnostic",
        "distributed",
    ),
    remote_hints=("remote", "anywhere", "worldwide", "distributed", "wfh",
                  "teletrabajo", "home-based", "work from home"),
    accept_modes=frozenset({"remote"}),
    accept_region_only_remote=True,
    # Antes _GLOBAL_HINTS de filters.py (D2(a): nunca había llegado a
    # config.py, así que un YAML no podía tocarlo). Usado SOLO dentro de
    # is_us_only() para descartar la hipótesis "es solo EE.UU." — no decide
    # 'ok' por sí solo, así que puede incluir "latam"/"europe"/etc sin
    # reintroducir el bug de arriba.
    non_us_signals=("worldwide", "anywhere", "global", "europe", "emea",
                    "latam", "americas", "international", "remote"),
)

_ROLE = RolePolicy(
    role_signals=(
        "engineer", "engineering", "developer", "programmer",
        "sre", "devops", "architect", "technical staff", "swe", "hacker",
        "backend", "back-end", "full stack", "fullstack", "full-stack",
        "platform", "infrastructure", "data scientist", "scientist",
        "desarrollador", "desarrolladora", "programador", "programadora",
        "ingeniero", "ingeniera", "ingeniería",
    ),
    non_role_signals=(
        "analyst", "consultant", "advisory", "recruiter", "recruiting",
        "sales", "account executive", "business development", "marketing",
        "customer success", "customer support", "designer", "design ",
        "writer", "copywriter", "accountant", "finance ", "legal", "counsel",
        "operations specialist", "office", "people partner", "talent",
        "teacher", "tutor", "instructor",
        "solutions engineer", "solution engineer", "sales engineer",
        "solutions architect", "solution architect", "technical support",
        "support engineer", "implementation engineer", "partner solutions",
        "field engineer", "developer advocate", "developer relations",
        "evangelist", "subject matter expert", "curriculum",
        "trainer", "professional services", "customer engineer",
        "technical account", "onboarding specialist", "flex solution",
    ),
    management_signals=(
        "manager", "engineering manager", "people manager", "delivery manager",
        "director", "head of", "vp ", "vice president", "cto", "chief",
        "scrum master", "product owner", "project manager", "program manager",
    ),
    senior_title_signals=(
        "senior", "sr.", "sr ", "lead", "tech lead", "team lead", "architect",
        "staff", "principal", "distinguished", "fellow",
        "6+ years", "7+ years", "8+ years", "10+ years",
    ),
    underqualified_signals=(
        "intern", "internship", "trainee", "apprentice", "working student",
        "becario", "prácticas",
    ),
    junior_signals=("junior", "jr.", "jr ", "entry level", "entry-level",
                    "graduate", "early career", "early-career", "0-1 years"),
    junior_years_range_signals=("0-2 years", "0 to 2 years", "1-2 years",
                                "1 to 2 years"),
    mid_signals=("mid level", "mid-level", "midlevel", "intermediate",
                "engineer ii", "engineer 2", "associate",
                "2-4 years", "2+ years", "3+ years", "3-5 years"),
    allowed_levels=frozenset({"junior", "mid", "unknown"}),
)

_COMPANY = CompanyKindPolicy(
    kind="product",
    consulting_signals=(
        "consultancy", "consulting firm", "it consulting", "outsourcing",
        "nearshore", "offshore", "staffing agency", "body shop",
        "staff augmentation", "client projects", "our clients' projects",
        "recruitment agency", "on behalf of our client",
    ),
    # No list of consultancies ships with the package: which employers to skip
    # is yours to decide (company.consulting_companies in config.yaml).
    consulting_companies=(),
    # Talent marketplaces post templates ("Senior X? Work remote!") that match
    # every stack; they are platforms, not roles, so they are filtered by default.
    staffing_platforms=(
        "lemon.io", "toptal", "andela", "turing", "x-team", "crossover",
        "remotebase", "mindrift", "outlier", "gun.io", "braintrust", "a.team",
        "arc.dev", "upwork", "fiverr", "freelancer.com", "remotasks", "appen",
        "telus international", "prolific", "surge ai", "micro1", "clickworker",
        "proxify",
    ),
    paywalled_domains=("weworkremotely.com", "flexjobs.com", "toptal.com/apply"),
)

_SIGNALS = SignalPolicy(
    positive=(
        "worldwide", "anywhere", "global", "work from anywhere",
        "international", "outside the us", "outside us", "remote - global",
        "europe", "european", "eu timezone", "emea", "cet", "cest",
        "latam", "remote anywhere", "fully remote",
        "contractor", "contract", "freelance", "independent contractor",
        "b2b", "1099",
        "open to international", "no sponsorship required",
        "no visa required", "location agnostic", "visa not required",
    ),
    negative=(
        "must be located in the us", "must reside in the us", "us only",
        "usa only", "united states only", "us-based only", "us based only",
        "domestic only", "within the us", "must live in the us",
        "authorized to work in the us", "us work authorization",
        "must be authorized to work in the united states",
        "sponsorship not available", "no visa sponsorship",
        "us citizen", "us citizenship", "security clearance",
        "active clearance", "secret clearance", "must be a us citizen",
    ),
    strong_intl=(
        "work from anywhere", "anywhere in the world", "hire anywhere",
        "open to international", "no visa required", "visa not required",
        "no sponsorship required", "any country", "globally distributed",
        "remote - worldwide", "remote (worldwide)", "location agnostic",
        "we hire globally", "fully distributed",
    ),
    exclusivity=(
        "no moonlighting", "moonlighting is not", "exclusivity",
        "exclusive service", "full-time exclusivity",
        "may not engage in any other employment", "no side projects",
        "not permitted to work for any other",
        "dedicate your full working time",
    ),
)

_SEARCH = SearchPolicy(
    search_terms=(
        "python", "fastapi", "backend", "flutter",
        "ai engineer", "llm", "mlops", "full stack",
        "typescript", "devops", "node",
    ),
    active_sources=(
        # linkedin, indeed and builtin scrape sites whose terms forbid it:
        # opt-in only (see README, "Ethics & ToS").
        "ats", "remoteok", "remotive", "himalayas",
        "hackernews", "workingnomads", "nodesk",
    ),
)

# El texto narrativo real vive en research/profile.md (I6: Profile.narrative
# es CONTENIDO ya leído, no una ruta). Aquí, en el default sin disco, va
# vacío a propósito: un clone recién hecho no tiene ese fichero, y
# `Profile.defaults()` tiene que poder construirse sin tocar el filesystem.
# `load_profile()` (Fase 1c) es quien lee research/profile.md y hace
# `replace(Profile.defaults(), narrative=texto)`.
_NARRATIVE = ""

PROFILE_DEFAULTS = Profile(
    experience=_EXPERIENCE, salary=_SALARY, stack=_STACK, geo=_GEO,
    role=_ROLE, company=_COMPANY, signals=_SIGNALS, narrative=_NARRATIVE,
    search=_SEARCH,
)
