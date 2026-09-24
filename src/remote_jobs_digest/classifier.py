"""Module `classifier` (design/CODEBASE-DESIGN.md §1, Seam 2).

La Implementation es la de `filters.py` original, migrada casi literal —
mismo orden de gates, mismos comentarios explicando cada decisión. Lo que
cambia es la Interface: `Classifier(profile)` recibe el perfil por
constructor en vez de leer `import config` como estado ambiente, así que dos
configuraciones pueden convivir en el mismo proceso (imprescindible para
tests table-driven y para `./rjs config --check` cuando compare la config
efectiva contra los defaults).

`filters.py` (Fase 1c) sobrevive como Adapter de deprecación:
`filters.process(job)` pasa a ser un wrapper de
`Classifier(Profile.defaults()).process(job)` mientras los 5 call sites
migran de uno en uno.

Verificado contra `tests/test_classifier_golden.py`: mismos verdicts,
oferta por oferta, que `filters.py` original con los mismos datos de
entrada (design/CODEBASE-DESIGN.md §6, fase 1b).
"""

from __future__ import annotations

import re

from remote_jobs_digest import platforms
from remote_jobs_digest.profile import lexicon
from remote_jobs_digest.profile.types import Profile
from remote_jobs_digest.sources.base import Job

# --- Parsing puro, sin dependencia de profile --------------------------------

_HOURLY_RE = re.compile(r"/\s*h|per hour|hourly|/hr|an hour", re.I)
_NUM_RE = re.compile(r"(\d[\d,\.]*)\s*([kK])?")
_CURRENCY_RE = re.compile(r"(USD|EUR|GBP|\$|€|£)")
_SALARY_CURRENCIES = {"$": "USD", "USD": "USD", "€": "EUR", "EUR": "EUR",
                      "£": "GBP", "GBP": "GBP"}

# Códigos de estado de EE.UU. sobre el texto SIN normalizar: hace falta la
# coma de ", CA" para no confundir "Berlin, DE" (Alemania) con Delaware.
_US_STATE_RE = re.compile(
    r",\s*(" + "|".join(lexicon.US_STATE_CODES) + r")\b", re.I)

# "5+ years", "3-5 years of experience", "at least 4 years", "minimum 2
# years". El número admite decimal: "1.5+ years" es común y sin el
# `(?:\.\d)?` el grupo capturaba solo el "5" — convertía un requisito de año
# y medio en uno de cinco. También en español ("3 años de experiencia").
_NUM = r"(\d{1,2}(?:[.,]\d)?)"
_YRS = r"(?:years?|yrs?\.?|años|anos)"
_YEARS_RES = [
    re.compile(rf"{_NUM}\s*(?:\+|plus)\s*{_YRS}", re.I),
    re.compile(rf"{_NUM}\s*(?:-|–|—|\bto\b|\ba\b)\s*{_NUM}\s*{_YRS}", re.I),
    re.compile(rf"(?:at least|minimum(?: of)?|min\.?|al menos"
               rf"|m[ií]nimo(?: de)?)\s*{_NUM}\s*{_YRS}", re.I),
    re.compile(rf"experiencia[^.\n]{{0,25}}?{_NUM}\s*{_YRS}", re.I),
    re.compile(rf"{_NUM}\s*{_YRS}\s*de\s*experiencia", re.I),
]


def _num_to_int(num: str, k: str) -> int | None:
    cleaned = num.replace(",", "")
    try:
        val = float(cleaned)
    except ValueError:
        return None
    if k:                       # "120k" -> 120000
        val *= 1000
    elif val < 1000:             # "120" suelto en un campo de salario ~ "120k"
        val *= 1000
    return int(val)


def _kw_in(keyword: str, haystack: str) -> bool:
    """Match con límites de palabra: 'ml' no debe casar con 'html' ni 'ai'
    con 'email'. Las keywords multi-palabra pasan tal cual."""
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])",
                     haystack) is not None


def detect_years_required(job: Job) -> int | None:
    """Años de experiencia que pide el anuncio, o None si no lo dice.

    Con varias cifras se queda con la MENOR: los anuncios listan un mínimo
    global y luego mínimos mayores por especialidad opcional. Redondea hacia
    abajo: "1.5+ years" -> 1, como lo lee un humano al decidir si aplica.
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


# --- Classifier ---------------------------------------------------------------

class Classifier:
    """Interface pequeña (design/CODEBASE-DESIGN.md §1):
      .process(job) -> Job
      .meets_stack_floor(job) -> bool
      .in_ideal_years(job) -> bool
    El resto son internal seams: pequeños, testeables pasándoles un
    `profile`, pero solo `Classifier` es lo que ven otros Modules.
    """

    def __init__(self, profile: Profile):
        self.profile = profile

    # --- Salario ---------------------------------------------------------

    def parse_salary(self, text: str) -> tuple[int | None, int | None, str]:
        """Extrae (min, max, moneda) de texto libre. Ignora tarifas/hora."""
        if not text:
            return None, None, ""
        if _HOURLY_RE.search(text):
            return None, None, ""
        cur_match = _CURRENCY_RE.search(text)
        currency = ""
        if cur_match:
            currency = _SALARY_CURRENCIES.get(cur_match.group(1), "USD")
        lo, hi = self.profile.salary.plausible_annual
        nums: list[int] = []
        for m in _NUM_RE.finditer(text):
            val = _num_to_int(m.group(1), m.group(2))
            if val and lo <= val <= hi:
                nums.append(val)
        if not nums:
            return None, None, currency
        # Sin símbolo reconocido, no se inventa uno: asumir USD colaba
        # salarios en monedas mucho más bajas contra el suelo más generoso.
        return min(nums), max(nums), currency

    def enrich_salary(self, job: Job) -> None:
        """Si la fuente no dio números, intenta sacarlos del texto."""
        if job.salary_min is None and job.salary_max is None:
            smin, smax, cur = self.parse_salary(job.salary_text or "")
            if smin or smax:
                job.salary_min, job.salary_max = smin, smax
                job.salary_currency = job.salary_currency or cur
        job.sort_salary = job.salary_max or job.salary_min or 0

    def salary_verdict(self, job: Job) -> str:
        """'ok' / 'low' / 'below_floor' / 'unknown'. Usa el suelo junior si
        `job.level == "junior"` — se llama DESPUÉS de detectar el nivel."""
        s = self.profile.salary
        top = job.salary_max or job.salary_min
        if not top:
            return "unknown"
        cur = (job.salary_currency or "")[:3]
        if not cur:
            # Sin moneda identificada no hay suelo contra el que comparar.
            return "unknown"
        if job.level == "junior":
            floor = s.floor_junior.get(cur)
            warn = s.warn_junior.get(cur)
        else:
            floor = s.floor.get(cur)
            warn = s.warn.get(cur)
        if floor is None:
            return "unknown"
        if top >= floor:
            return "ok"
        if warn is not None and top >= warn:
            return "low"
        return "below_floor"

    # --- Relevancia de stack ----------------------------------------------

    def score_relevance(self, job: Job) -> None:
        stk = self.profile.stack
        title = job.title.lower()
        tags = " ".join(job.tags).lower()
        title_hits = {kw for kw in stk.keywords if kw in title}
        tag_hits = {kw for kw in stk.high_signal if kw in tags}
        # Gate SOLO por título: los tags genéricos no deciden inclusión.
        job.is_relevant = bool(title_hits)
        job.relevance = 2 * len(title_hits) + len(tag_hits)

    def score_stack(self, job: Job) -> None:
        """Encaje ponderado 0-score_cap con el perfil.

        Las keywords fuertes puntúan aparezcan donde aparezcan; las débiles
        solo si están en el título o los tags. Sin esa distinción, el
        boilerplate de cualquier oferta grande disparaba el score al máximo
        en puestos que no tienen nada que ver.
        """
        stk = self.profile.stack
        strong_hay = (f" {job.title.lower()} {job.description.lower()} "
                      f"{' '.join(job.tags).lower()} ")
        weak_hay = f" {job.title.lower()} {' '.join(job.tags).lower()} "
        score, hits = 0, []
        for cat in stk.weighted:
            if (any(_kw_in(kw, strong_hay) for kw in cat.strong)
                    or any(_kw_in(kw, weak_hay) for kw in cat.weak)):
                score += cat.weight
                hits.append(cat.label)
        job.stack_score, job.stack_hits = min(score, stk.score_cap), hits

    def meets_stack_floor(self, job: Job) -> bool:
        return job.stack_score >= self.profile.stack.min_score

    def in_ideal_years(self, job: Job) -> bool:
        if job.years_required is None:
            return False
        lo, hi = self.profile.experience.ideal_min, self.profile.experience.ideal_max
        return lo <= job.years_required <= hi

    # --- Aptitud geográfica -------------------------------------------------

    def is_us_only(self, location: str) -> bool:
        """True si la ubicación restringe exclusivamente a EE.UU."""
        if not location:
            return False
        loc = location.lower()
        # "USA timezones" es una restricción de HORARIO, no de residencia.
        if "timezone" in loc:
            return False
        if any(g in loc for g in self.profile.geo.non_us_signals):
            return False
        parts = [p.strip() for p in re.split(r"[,;/&|]| and ", loc) if p.strip()]
        if not parts:
            return False
        us_like = 0
        for p in parts:
            if any(tok in p for tok in lexicon.US_ONLY_TOKENS) or p in ("us", "u.s"):
                us_like += 1
        return us_like == len(parts) and us_like > 0

    def geo_verdict(self, job: Job) -> str:
        """Elegibilidad geográfica desde `profile.geo.home_hints` —
        eliminatoria.

        Devuelve:
          'ok'               puede trabajarse desde tu zona (home)
          'onsite_local'     presencial/híbrido en tu zona — viable pero no remoto
          'remote_other_eu'  remoto, pero exige residir en otro país de la región
          'onsite'           presencial fuera de la región: implica mudarse
          'us_only'          restringido a EE.UU./Norteamérica
          'remote_unclear'   remoto, pero ninguna región reconocida (incluye
                             "Remote" a secas y regiones sin listar como LATAM)
          'unknown'          sin datos

        La distinción clave: estar en la región (`region_hints`) no es lo
        mismo que poder trabajar desde `home_hints`. Un país concreto de la
        región exige residir allí ("Remote Poland"), y solo la región
        amplia o el propio home te incluyen sin más.
        """
        g = self.profile.geo
        # "Remote - US", "Remote-US", "Remote (US)", "Remote, US": colapsar
        # separadores a espacios evita mantener una variante por estilo.
        loc_raw = (job.location or "").lower()
        loc = re.sub(r"[^a-z0-9]+", " ", loc_raw).strip()
        text = f"{job.title} {job.description}".lower()

        # La UBICACIÓN manda. La descripción solo añade señal si es
        # inequívoca: buscar "global" en el cuerpo daba por remota cualquier
        # oferta de una empresa que se describe como "a global company".
        if any(s in text for s in self.profile.signals.negative):
            return "us_only"
        if any(h in loc for h in lexicon.REMOTE_BUT_NORTH_AMERICA):
            return "us_only"

        is_remote = any(h in loc for h in g.remote_hints)

        # Home: vale en cualquier modalidad, porque ya vives ahí.
        if any(h in loc for h in g.home_hints):
            return "ok" if is_remote else "onsite_local"
        # Región amplia: home queda dentro por definición.
        if any(h in loc for h in g.region_hints):
            return "ok"
        if any(h in loc for h in g.global_hints):
            return "ok"
        # País concreto de la región que no es home.
        if any(h in loc for h in g.away_hints):
            return "remote_other_eu" if is_remote else "onsite"
        # Fuera de la región: ni siquiera con "remote" sirve.
        if any(h in loc for h in g.blocked_hints):
            return "onsite"
        if (self.is_us_only(job.location)
                or _US_STATE_RE.search(loc_raw)
                or any(c in loc for c in lexicon.US_CITIES)):
            return "us_only"
        if is_remote:
            # Remoto, pero ninguna lista de arriba reconoció una región: sin
            # base para asumir que incluye home. Solo pasa a 'ok' si el
            # anuncio promete alcance global explícito en el texto.
            if g.accept_region_only_remote and any(
                    s in text for s in self.profile.signals.strong_intl):
                return "ok"
            return "remote_unclear"
        if not loc:
            return "ok" if any(
                s in text for s in self.profile.signals.strong_intl) else "unknown"
        return "onsite"

    # --- Nivel / rol -----------------------------------------------------

    def detect_level(self, job: Job, years: int | None = None) -> str:
        """Nivel por TÍTULO. Cortes eliminatorios ('overqualified',
        'underqualified') se evalúan antes que senior/mid/junior."""
        r = self.profile.role
        # Título del board MÁS el arranque de la descripción: los ATS a
        # veces recortan el título mientras el anuncio empieza con el real.
        head = job.description[:110]
        title = " " + re.sub(r"[^a-z0-9]+", " ",
                             f"{job.title} {head}".lower()).strip() + " "

        def hit(signals: tuple[str, ...]) -> bool:
            # Envuelto en espacios: 'staff' no debe casar con 'staffing'.
            return any(f" {re.sub(r'[^a-z0-9]+', ' ', s).strip()} " in title
                       for s in signals)

        if hit(r.management_signals):
            return "overqualified"
        if hit(r.underqualified_signals):
            return "underqualified"
        # Título de nivel alto: manda lo que pida el anuncio, no la etiqueta.
        if hit(r.senior_title_signals):
            if years is not None and years <= self.profile.experience.max_required:
                return "mid" if years >= self.profile.experience.ideal_min else "junior"
            return "overqualified"
        # Nota: el original tenía un SENIOR_SIGNALS separado de
        # SENIOR_TITLE_SIGNALS, pero siempre vacío ("absorbido por
        # OVERQUALIFIED_SIGNALS", su propio comentario) — código muerto por
        # diseño, no migrado aquí.
        # Rangos junior tipo "0-2 years" van ANTES que mid_signals: "0-2
        # years" contiene la subcadena "2 years", que `hit()` (substring, no
        # regex de rango) confunde con la señal mid "2+ years"/"2-4 years".
        if hit(r.junior_years_range_signals):
            return "junior"
        if hit(r.mid_signals):
            return "mid"
        if hit(r.junior_signals):
            return "junior"
        return "unknown"

    def is_engineering_role(self, job: Job) -> bool:
        """El título debe ser de ingeniería. La descripción no basta:
        cualquier oferta tech menciona el stack en el 'sobre nosotros'."""
        title = f" {job.title.lower()} "
        r = self.profile.role
        if any(s in title for s in r.non_role_signals):
            return False
        return any(s in title for s in r.role_signals)

    def platform_match(self, job: Job) -> tuple[str, str] | None:
        """Intermediary platform (marketplace, AI data work, reposter), not an employer."""
        c = self.profile.company
        return platforms.match(job.company, c.skip_platforms, c.staffing_platforms,
                               c.allow_platforms)

    # --- Clasificación -----------------------------------------------------

    def classify(self, job: Job) -> None:
        """APTA / REVISAR / DESCARTADA.

        Orden deliberado: primero los eliminatorios (geografía, nivel,
        modelo de empresa, suelo salarial) y solo después se premia el
        encaje. Así una oferta de consultoría con stack perfecto nunca sale
        como APTA si `company.kind == "product"`.
        """
        p = self.profile
        text = " ".join([job.title, job.description, job.location]).lower()
        neg = [s for s in p.signals.negative if s in text]
        pos = [s for s in p.signals.positive if s in text]

        job.has_exclusivity = any(s in text for s in p.signals.exclusivity)
        company_low = job.company.lower()
        # Se calcula siempre (informativo), pero solo descarta según
        # company.kind — el original siempre descartaba porque su único
        # "kind" implícito era "product".
        job.is_consulting = (any(s in text for s in p.company.consulting_signals)
                             or any(c in company_low
                                    for c in p.company.consulting_companies))
        job.salary_flag = self.salary_verdict(job)
        job.geo = self.geo_verdict(job)
        job.years_required = detect_years_required(job)
        job.is_ai_role = "ai_llm" in job.stack_hits

        # --- Eliminatorios ------------------------------------------------
        if neg:
            job.verdict, job.reason = "DESCARTADA", f"Señales negativas: {neg[:3]}"
            return
        if job.level == "overqualified":
            job.verdict = "DESCARTADA"
            job.reason = "Senior/lead/gestión — el perfil buscado es otro nivel"
            return
        if job.level == "underqualified":
            job.verdict, job.reason = "DESCARTADA", "Beca/prácticas"
            return
        if job.years_required and job.years_required > p.experience.max_required:
            job.verdict = "DESCARTADA"
            job.reason = f"Pide {job.years_required}+ años (máx. {p.experience.max_required})"
            return
        if not self.is_engineering_role(job):
            job.verdict, job.reason = "DESCARTADA", "No es un rol de ingeniería"
            return
        if any(d in job.url.lower() for d in p.company.paywalled_domains):
            job.verdict = "DESCARTADA"
            job.reason = "Portal de pago: no se puede aplicar sin suscripción"
            return
        if hit := self.platform_match(job):
            job.verdict = "DESCARTADA"
            job.reason = f"Platform, not an employer ({hit[0]}: {hit[1]})"
            return
        if p.company.kind == "product" and job.is_consulting:
            job.verdict = "DESCARTADA"
            job.reason = "Consultoría/outsourcing — buscas empresa de producto"
            return
        if p.company.kind == "consultancy" and not job.is_consulting:
            job.verdict = "DESCARTADA"
            job.reason = "No es consultoría — buscas específicamente consultoras"
            return
        if job.salary_flag == "below_floor":
            job.verdict = "DESCARTADA"
            job.reason = f"Bajo el suelo: {job.sort_salary:,} {job.salary_currency}"
            return

        # --- Geografía: solo cuando el puesto en sí ya ha pasado -----------
        if job.geo == "onsite":
            job.verdict = "DESCARTADA"
            job.reason = (f"Presencial fuera de tu zona ({job.location!r}): "
                          f"implica mudarse")
            return
        if job.geo == "us_only":
            strong = [s for s in p.signals.strong_intl if s in text]
            if not strong:
                job.verdict = "DESCARTADA"
                job.reason = f"Solo EE.UU./Norteamérica: {job.location!r}"
                return
            job.verdict = "REVISAR"
            job.reason = (f"US pero contrata fuera ({strong[:2]}) — "
                          f"viable como contractor; verificar overlap horario")
            return
        if job.years_required and job.years_required >= p.experience.stretch:
            job.verdict = "REVISAR"
            job.reason = (f"Pide {job.years_required} años — defendible; "
                          f"stack {job.stack_score}/{p.stack.score_cap}")
            return

        # --- Encaje ---------------------------------------------------------
        bits = [f"stack {job.stack_score}/{p.stack.score_cap}"]
        if job.stack_hits:
            bits.append("+".join(job.stack_hits))
        if job.is_ai_role:
            bits.append("🤖 rol AI")
        if job.years_required:
            lo, hi = p.experience.ideal_min, p.experience.ideal_max
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
                        f"verificar si incluye tu zona")
            job.reason = "; ".join(bits)
            return

        geo_ok = job.geo == "ok"
        if (geo_ok and self.meets_stack_floor(job)
                and job.salary_flag != "low" and not job.has_exclusivity):
            job.verdict = "APTA"
        else:
            job.verdict = "REVISAR"
            if not self.meets_stack_floor(job):
                bits.append("stack por debajo del umbral")
        job.reason = "; ".join(bits)

    def process(self, job: Job) -> Job:
        self.enrich_salary(job)
        self.score_relevance(job)
        self.score_stack(job)
        # Los años se calculan ANTES del nivel: detect_level los necesita
        # para decidir si un "Senior" del título lo es de verdad.
        job.years_required = detect_years_required(job)
        job.level = self.detect_level(job, job.years_required)
        self.classify(job)
        return job
