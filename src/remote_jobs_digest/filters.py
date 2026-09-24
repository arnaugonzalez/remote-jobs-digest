"""Clasificación, relevancia y parseo de salario.

Enriquch cada Job con: relevance (stack), verdict (aptitud internacional),
salario normalizado y la clave de orden sort_salary.
"""

from __future__ import annotations

import re

from remote_jobs_digest import config, platforms
from remote_jobs_digest.sources.base import Job

# --- Salario --------------------------------------------------------------

_HOURLY_RE = re.compile(r"/\s*h|per hour|hourly|/hr|an hour", re.I)
_NUM_RE = re.compile(r"(\d[\d,\.]*)\s*([kK])?")
_CURRENCY_RE = re.compile(r"(USD|EUR|GBP|\$|€|£)")


def _num_to_int(num: str, k: str) -> int | None:
    cleaned = num.replace(",", "")
    try:
        val = float(cleaned)
    except ValueError:
        return None
    if k:                       # "120k" -> 120000
        val *= 1000
    # Heurística: "120" suelto en un campo de salario casi siempre es "120k".
    elif val < 1000:
        val *= 1000
    return int(val)


def parse_salary(text: str) -> tuple[int | None, int | None, str]:
    """Extrae (min, max, moneda) de texto libre. Ignora tarifas por hora."""
    if not text:
        return None, None, ""
    if _HOURLY_RE.search(text):
        return None, None, ""    # no mezclamos tarifas horarias con anuales
    cur_match = _CURRENCY_RE.search(text)
    currency = ""
    if cur_match:
        currency = config.SALARY_CURRENCIES.get(cur_match.group(1), "USD")
    nums: list[int] = []
    for m in _NUM_RE.finditer(text):
        val = _num_to_int(m.group(1), m.group(2))
        if val and config.MIN_PLAUSIBLE_ANNUAL <= val <= config.MAX_PLAUSIBLE_ANNUAL:
            nums.append(val)
    if not nums:
        return None, None, currency
    # Sin símbolo de moneda reconocido, no inventamos una: asumir USD (el
    # suelo más generoso) colaba salarios en monedas mucho más bajas —
    # "60.000 PLN" (~14k€) aprobaba el suelo de 60.000 USD.
    return min(nums), max(nums), currency


def enrich_salary(job: Job) -> None:
    """Si la fuente no dio números, intenta sacarlos del texto."""
    if job.salary_min is None and job.salary_max is None:
        smin, smax, cur = parse_salary(job.salary_text or "")
        if smin or smax:
            job.salary_min, job.salary_max = smin, smax
            job.salary_currency = job.salary_currency or cur
    # Clave de orden: el máximo conocido (o el mínimo si solo hay uno).
    job.sort_salary = job.salary_max or job.salary_min or 0


# --- Relevancia de stack --------------------------------------------------

def score_relevance(job: Job) -> None:
    title = job.title.lower()
    tags = " ".join(job.tags).lower()
    title_hits = {kw for kw in config.TECH_KEYWORDS if kw in title}
    tag_hits = {kw for kw in config.HIGH_SIGNAL_KEYWORDS if kw in tags}
    # Gate SOLO por título: RemoteOK cuelga tags genéricos (hasta "python") a
    # ofertas que no tocan tu stack, así que los tags no deciden inclusión.
    job.is_relevant = bool(title_hits)
    # Score: el título pesa el doble; los tags solo afinan el orden.
    job.relevance = 2 * len(title_hits) + len(tag_hits)


def _kw_in(keyword: str, haystack: str) -> bool:
    """Match con límites de palabra: 'ml' no debe casar con 'html' ni 'ai'
    con 'email'. Las keywords multi-palabra pasan tal cual."""
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])",
                     haystack) is not None


def score_stack(job: Job) -> None:
    """Encaje ponderado 0-10 con el perfil (config.STACK_WEIGHTS, handoff §4).

    Las keywords fuertes puntúan aparezcan donde aparezcan; las débiles solo si
    están en el título o los tags. Sin esa distinción, el boilerplate de
    cualquier oferta grande ("trabajamos con Python, Go, AWS y ML") disparaba
    el score al máximo en puestos que no tienen nada que ver.
    """
    strong_hay = (f" {job.title.lower()} {job.description.lower()} "
                  f"{' '.join(job.tags).lower()} ")
    weak_hay = f" {job.title.lower()} {' '.join(job.tags).lower()} "
    score, hits = 0, []
    for weight, label, strong, weak in config.STACK_WEIGHTS:
        if (any(_kw_in(kw, strong_hay) for kw in strong)
                or any(_kw_in(kw, weak_hay) for kw in weak)):
            score += weight
            hits.append(label)
    # Con 4 categorías a peso 3 (backend_python/ai_llm/infra/typescript_node)
    # una oferta que las toque todas sumaría más de 10; se tope aquí para que
    # "stack X/10" en los informes/digest siga siendo literal.
    job.stack_score, job.stack_hits = min(score, 10), hits


# --- Aptitud internacional ------------------------------------------------

_GLOBAL_HINTS = ("worldwide", "anywhere", "global", "europe", "emea",
                 "latam", "americas", "international", "remote")
_US_TOKENS = ("united states", "usa", "u.s.", "us-only", "us only",
              "us based", "us-based")


_US_STATE_RE = re.compile(
    r",\s*(" + "|".join(config.US_STATE_CODES) + r")\b", re.I)


def is_us_only(location: str) -> bool:
    """True si la ubicación restringe exclusivamente a EE.UU."""
    if not location:
        return False
    loc = location.lower()
    # "USA timezones" es una restricción de HORARIO, no de residencia: un
    # contractor fuera de US puede cumplirla. No la tratamos como US-only.
    if "timezone" in loc:
        return False
    if any(g in loc for g in _GLOBAL_HINTS):
        return False
    # Trocea por separadores comunes y mira si TODOS los tokens son US.
    parts = [p.strip() for p in re.split(r"[,;/&|]| and ", loc) if p.strip()]
    if not parts:
        return False
    us_like = 0
    for p in parts:
        if any(tok in p for tok in _US_TOKENS) or p in ("us", "u.s"):
            us_like += 1
    return us_like == len(parts) and us_like > 0


def geo_verdict(job: Job) -> str:
    """Elegibilidad geográfica desde España — eliminatoria (handoff §4).

    Devuelve:
      'ok'               puede trabajarse desde España
      'onsite_local'     presencial/híbrido en España — viable pero no remoto
      'remote_other_eu'  remoto, pero exige residir en otro país de la UE
      'onsite'           presencial fuera de España: implica mudarse
      'us_only'          restringido a EE.UU./Norteamérica
      'remote_unclear'   remoto, pero ninguna región reconocida (incluye
                         "Remote" a secas y regiones sin listar como LATAM):
                         sin base para asumir que incluye España
      'unknown'          sin datos

    La distinción clave: **estar en Europa no es poder trabajar desde España**.
    "London, UK" es tan inviable como "San Francisco, CA" si el puesto es
    presencial, y "Remote Poland" exige contrato polaco. Solo la región amplia
    (Europe/EMEA) o España explícita te incluyen.
    """
    # Normalizamos separadores: los ATS escriben la misma restricción como
    # "Remote - US", "Remote-US", "Remote (US)" y "Remote, US". Colapsarlos a
    # espacios evita mantener una variante por cada estilo de guion.
    loc_raw = (job.location or "").lower()
    loc = re.sub(r"[^a-z0-9]+", " ", loc_raw).strip()
    text = f"{job.title} {job.description}".lower()

    # La UBICACIÓN manda. La descripción solo puede añadir señal si es
    # inequívoca: buscar "global" en el cuerpo daba por remota cualquier oferta
    # de una empresa que se describe como "a global company" — y así entraban
    # puestos presenciales en San Francisco.
    if any(s in text for s in config.NEGATIVE_SIGNALS):
        return "us_only"
    if any(h in loc for h in config.REMOTE_BUT_AMERICAS):
        return "us_only"

    is_remote = any(h in loc for h in config.REMOTE_HINTS)

    # España: vale en cualquier modalidad, porque ya vive aquí.
    if any(h in loc for h in config.SPAIN_HINTS):
        return "ok" if is_remote else "onsite_local"
    # Región amplia (Europe/EMEA): España queda dentro.
    if any(h in loc for h in config.EU_REGION_HINTS):
        return "ok"
    if any(h in loc for h in config.GLOBAL_LOCATION_HINTS):
        return "ok"
    # País concreto de la UE que no es España.
    if any(h in loc for h in config.EU_COUNTRY_HINTS):
        return "remote_other_eu" if is_remote else "onsite"
    # País fuera de la UE: ni siquiera con "remote" sirve — ata la residencia.
    if any(h in loc for h in config.NON_EU_COUNTRY_HINTS):
        return "onsite"
    # El código de estado se busca sobre el texto SIN normalizar: hace falta la
    # coma de ", CA" para no confundir "Berlin, DE" (Alemania) con Delaware.
    if (is_us_only(job.location)
            or _US_STATE_RE.search(loc_raw)
            or any(c in loc for c in config.US_CITIES)):
        return "us_only"
    if is_remote:
        # Remoto, pero ninguna lista de arriba reconoció una región (incluye
        # "Remote" a secas y regiones sin listar como LATAM/APAC): no hay
        # base para asumir que incluye España. Antes esto devolvía "ok"
        # directamente — "Remote — LATAM" pasaba como si fuera desde
        # cualquier sitio. Solo pasa a 'ok' si el anuncio promete alcance
        # global explícito en el texto.
        if any(s in text for s in config.STRONG_INTL_SIGNALS):
            return "ok"
        return "remote_unclear"
    if not loc:
        # Sin ubicación solo salva una promesa explícita de contratar fuera.
        return "ok" if any(s in text for s in config.STRONG_INTL_SIGNALS) \
            else "unknown"
    # Ciudad concreta, no-US, sin mención de remoto -> presencial.
    return "onsite"


def detect_level(job: Job, years: int | None = None) -> str:
    """Nivel por TÍTULO. El orden importa: los cortes eliminatorios
    ('overqualified' = gestión/staff+, 'underqualified' = becario) se evalúan
    antes que senior/mid/junior, que sí son niveles aceptables.

    Se compara sobre el título normalizado (puntuación -> espacios) porque los
    títulos reales traen '+', '/' y ',': "Staff+ Software Engineer" no casaba
    con la señal "staff " y colaba un rol de gestión.
    """
    # Se mira el título del board MÁS el arranque de la descripción: los ATS
    # a veces recortan el título ("MLOps Engineer") mientras el anuncio empieza
    # con el real ("Senior ML / MLOps Engineer"). Sin esto colaban seniors.
    head = job.description[:110]
    title = " " + re.sub(r"[^a-z0-9]+", " ",
                         f"{job.title} {head}".lower()).strip() + " "
    def hit(signals: list[str]) -> bool:
        # Envuelto en espacios: 'staff' no debe casar con 'staffing'.
        return any(f" {re.sub(r'[^a-z0-9]+', ' ', s).strip()} " in title
                   for s in signals)
    # Gestión: fuera pase lo que pase.
    if hit(config.MANAGEMENT_SIGNALS):
        return "overqualified"
    if hit(config.UNDERQUALIFIED_SIGNALS):
        return "underqualified"
    # Título de nivel alto: manda lo que pida el anuncio, no la etiqueta. Un
    # "Senior Engineer" con "3+ years" es aplicable; sin años declarados se
    # asume que el título dice la verdad.
    if hit(config.SENIOR_TITLE_SIGNALS):
        if years is not None and years <= config.MAX_YEARS_REQUIRED:
            return "mid" if years >= config.IDEAL_YEARS[0] else "junior"
        return "overqualified"
    if hit(config.SENIOR_SIGNALS):
        return "senior"
    # Rangos junior tipo "0-2 years"/"1-2 years" van ANTES que MID_SIGNALS:
    # "0-2 years" contiene la subcadena "2 years", que `hit()` (substring, no
    # regex de rango) confunde con la señal mid "2+ years"/"2-4 years". Sin
    # este corte, un "Junior Developer, 0-2 years" se leía como mid — rompía
    # justo el suelo salarial junior más bajo (2026-08-06).
    if hit(config.JUNIOR_YEARS_RANGE_SIGNALS):
        return "junior"
    if hit(config.MID_SIGNALS):
        return "mid"
    if hit(config.JUNIOR_SIGNALS):
        return "junior"
    return "unknown"


# "5+ years", "3-5 years of experience", "at least 4 years", "minimum 2 years".
# El número admite decimal: "1.5+ years" es común y, sin el `(?:\.\d)?`, el
# grupo capturaba solo el "5" — convertía un requisito de año y medio en uno de
# cinco y descartaba la mejor oferta del run.
_NUM = r"(\d{1,2}(?:[.,]\d)?)"
# También en español: con la fuente linkedin (geo Spain) muchos anuncios dicen
# "3 años de experiencia" o "experiencia mínima de 3 años", no "3+ years".
_YRS = r"(?:years?|yrs?\.?|años|anos)"
_YEARS_RES = [
    re.compile(rf"{_NUM}\s*(?:\+|plus)\s*{_YRS}", re.I),
    re.compile(rf"{_NUM}\s*(?:-|–|—|\bto\b|\ba\b)\s*{_NUM}\s*{_YRS}", re.I),
    re.compile(rf"(?:at least|minimum(?: of)?|min\.?|al menos"
               rf"|m[ií]nimo(?: de)?)\s*{_NUM}\s*{_YRS}", re.I),
    re.compile(rf"experiencia[^.\n]{{0,25}}?{_NUM}\s*{_YRS}", re.I),
    re.compile(rf"{_NUM}\s*{_YRS}\s*de\s*experiencia", re.I),
]


def detect_years_required(job: Job) -> int | None:
    """Años de experiencia que pide el anuncio, o None si no lo dice.

    Cuando aparecen varias cifras se queda con la MENOR: los anuncios suelen
    listar un mínimo global ("3+ years of software engineering") y luego
    mínimos mayores por especialidad opcional ("5+ years with Kubernetes").
    Lo que decide si puedes aplicar es el primero.

    Redondea hacia abajo: "1.5+ years" -> 1, que es como lo lee un humano al
    decidir si aplica.
    """
    text = job.description.lower()
    if not text:
        return None
    found: list[float] = []
    for rx in _YEARS_RES:
        for m in rx.finditer(text):
            for g in m.groups():
                if not g:
                    continue
                try:
                    n = float(g.replace(",", "."))
                except ValueError:
                    continue
                if 0 < n <= 20:       # descarta "2024 years" y ruido
                    found.append(n)
    return int(min(found)) if found else None


def is_engineering_role(job: Job) -> bool:
    """El título debe ser de ingeniería. La descripción no basta: cualquier
    oferta de una empresa tech menciona el stack en el 'sobre nosotros'."""
    title = f" {job.title.lower()} "
    if any(s in title for s in config.NON_ROLE_SIGNALS):
        return False
    return any(s in title for s in config.ROLE_SIGNALS)


def platform_match(job: Job) -> tuple[str, str] | None:
    """Intermediary platform (marketplace, AI data work, reposter), not an employer."""
    c = config.PROFILE.company
    return platforms.match(job.company, c.skip_platforms, c.staffing_platforms,
                           c.allow_platforms)


def salary_verdict(job: Job) -> str:
    """'ok' / 'low' / 'below_floor' / 'unknown' contra los suelos del §4.

    Usa el suelo junior (más bajo) si `job.level == "junior"` — se llama
    DESPUÉS de `detect_level()` en `process()`, así que el nivel ya está
    fijado cuando esto corre."""
    top = job.salary_max or job.salary_min
    if not top:
        return "unknown"
    cur = (job.salary_currency or "")[:3]
    if not cur:
        # Sin moneda identificada no hay suelo contra el que comparar —
        # asumir USD aquí era el segundo sitio donde colaba una moneda no
        # reconocida contra el suelo más generoso (ver parse_salary).
        return "unknown"
    if job.level == "junior":
        floor = config.MIN_SALARY_JUNIOR.get(cur)
        warn = config.WARN_SALARY_JUNIOR.get(cur)
    else:
        floor = config.MIN_SALARY.get(cur)
        warn = config.WARN_SALARY.get(cur)
    if floor is None:
        return "unknown"
    if top >= floor:
        return "ok"
    if warn is not None and top >= warn:
        return "low"
    return "below_floor"


def classify(job: Job) -> None:
    """APTA / REVISAR / DESCARTADA según los criterios del handoff §4.

    Orden deliberado: primero los eliminatorios (geografía, nivel, modelo de
    empresa, suelo salarial) y solo después se premia el encaje. Así una oferta
    de consultoría con stack perfecto nunca sale como APTA.
    """
    text = " ".join([job.title or "", job.description or "", job.location or ""]).lower()
    neg = [s for s in config.NEGATIVE_SIGNALS if s in text]
    pos = [s for s in config.POSITIVE_SIGNALS if s in text]

    # Marcadores informativos (no deciden solos, pero viajan en el informe).
    job.has_exclusivity = any(s in text for s in config.EXCLUSIVITY_SIGNALS)
    company_low = job.company.lower()
    job.is_consulting = (any(s in text for s in config.CONSULTING_SIGNALS)
                         or any(c in company_low
                                for c in config.CONSULTING_COMPANIES))
    job.salary_flag = salary_verdict(job)
    job.geo = geo_verdict(job)
    job.years_required = detect_years_required(job)
    job.is_ai_role = "ai_llm" in job.stack_hits

    # --- Eliminatorios --------------------------------------------------
    if neg:
        job.verdict, job.reason = "DESCARTADA", f"Señales negativas: {neg[:3]}"
        return
    if job.level == "overqualified":
        job.verdict = "DESCARTADA"
        job.reason = "Senior/lead/gestión — el perfil es mid"
        return
    if job.level == "underqualified":
        job.verdict, job.reason = "DESCARTADA", "Beca/prácticas"
        return
    if job.years_required and job.years_required > config.MAX_YEARS_REQUIRED:
        job.verdict = "DESCARTADA"
        job.reason = f"Pide {job.years_required}+ años (máx. {config.MAX_YEARS_REQUIRED})"
        return
    if not is_engineering_role(job):
        job.verdict, job.reason = "DESCARTADA", "No es un rol de ingeniería"
        return
    if any(d in job.url.lower() for d in config.PAYWALLED_DOMAINS):
        job.verdict = "DESCARTADA"
        job.reason = "Portal de pago: no se puede aplicar sin suscripción"
        return
    if hit := platform_match(job):
        job.verdict = "DESCARTADA"
        job.reason = f"Platform, not an employer ({hit[0]}: {hit[1]})"
        return
    if job.is_consulting:
        job.verdict = "DESCARTADA"
        job.reason = "Consultoría/outsourcing — el modelo del que se sale (§4)"
        return
    if job.salary_flag == "below_floor":
        job.verdict = "DESCARTADA"
        job.reason = f"Bajo el suelo: {job.sort_salary:,} {job.salary_currency}"
        return

    # --- Geografía: solo cuando el puesto en sí ya ha pasado ------------
    # Va DESPUÉS a propósito. Cuando el rescate de us_only iba primero, su
    # `return` dejaba pasar Staff Engineers de 12 años y analistas financieros
    # con solo que el anuncio dijera "work from anywhere".
    if job.geo == "onsite":
        job.verdict = "DESCARTADA"
        job.reason = (f"Presencial fuera de España ({job.location!r}): "
                      f"implica mudarse")
        return
    if job.geo == "us_only":
        strong = [s for s in config.STRONG_INTL_SIGNALS if s in text]
        if not strong:
            job.verdict = "DESCARTADA"
            job.reason = f"Solo EE.UU./Norteamérica: {job.location!r}"
            return
        # Dice explícitamente que contrata fuera: viable como contractor con
        # overlap parcial, pero nunca APTA automática (§4 lo marca como ⚠️).
        job.verdict = "REVISAR"
        job.reason = (f"US pero contrata fuera ({strong[:2]}) — "
                      f"viable como contractor; verificar overlap horario")
        return
    # Rango, no igualdad exacta: con '==' esto solo era correcto porque hoy
    # STRETCH_YEARS == MAX_YEARS_REQUIRED (5 == 5) por casualidad. Si algún
    # día dejan de coincidir, pedir MÁS años que STRETCH_YEARS (pero aún
    # dentro de MAX_YEARS_REQUIRED, que ya se filtró arriba) debe seguir
    # cayendo en REVISAR, no saltarse directo al scoring normal.
    if job.years_required and job.years_required >= config.STRETCH_YEARS:
        # Stretch band: defensible, not an automatic MATCH.
        job.verdict = "REVISAR"
        job.reason = (f"asks {job.years_required} years (stretch band); "
                      f"stack {job.stack_score}/10")
        return

    # --- Encaje ---------------------------------------------------------
    bits = [f"stack {job.stack_score}/10"]
    if job.stack_hits:
        bits.append("+".join(job.stack_hits))
    if job.is_ai_role:
        bits.append("🤖 rol AI")
    if job.years_required:
        lo, hi = config.IDEAL_YEARS
        mark = "✔" if lo <= job.years_required <= hi else ""
        bits.append(f"pide {job.years_required} años{mark}")
    else:
        bits.append("años no especificados")
    if job.salary_flag == "ok":
        bits.append(f"salario {job.sort_salary:,} {job.salary_currency} ✔")
    elif job.salary_flag == "low":
        bits.append(f"salario justo ({job.sort_salary:,} {job.salary_currency})")
    else:
        bits.append("sin transparencia salarial")
    if job.has_exclusivity:
        bits.append("⚠ cláusula de exclusividad")
    bits.append(f"geo {job.geo}" + (f" {pos[:2]}" if pos else ""))

    # Solo 'ok' habilita APTA. Los dos casos intermedios son viables pero
    # exigen una decisión personal, así que van a REVISAR con el motivo claro.
    if job.geo == "remote_other_eu":
        job.verdict = "REVISAR"
        bits.append(f"⚠ remoto pero exige residir en {job.location!r}")
        job.reason = "; ".join(bits)
        return
    if job.geo == "onsite_local":
        job.verdict = "REVISAR"
        bits.append(f"⚠ presencial/híbrido en tu zona ({job.location!r}), "
                    f"no 100% remoto")
        job.reason = "; ".join(bits)
        return
    if job.geo == "remote_unclear":
        job.verdict = "REVISAR"
        bits.append(f"⚠ remoto sin región clara ({job.location!r}): "
                    f"verificar si incluye España/UE")
        job.reason = "; ".join(bits)
        return

    geo_ok = job.geo == "ok"
    if geo_ok and job.stack_score >= config.MIN_STACK_SCORE \
            and job.salary_flag != "low" and not job.has_exclusivity:
        job.verdict = "APTA"
    else:
        job.verdict = "REVISAR"
        if job.stack_score < config.MIN_STACK_SCORE:
            bits.append("stack por debajo del umbral")
    job.reason = "; ".join(bits)


def process(job: Job) -> Job:
    enrich_salary(job)
    score_relevance(job)
    score_stack(job)
    # Los años se calculan ANTES del nivel: detect_level los necesita para
    # decidir si un "Senior" del título lo es de verdad.
    job.years_required = detect_years_required(job)
    job.level = detect_level(job, job.years_required)
    classify(job)
    return job
