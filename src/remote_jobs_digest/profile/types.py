"""El vocabulario del dominio de búsqueda, congelado.

Ver design/CODEBASE-DESIGN.md §3 para el diseño y sus invariantes (numerados
aquí como I1..I12, mismos números que el documento). Este módulo no toca
disco ni conoce YAML/TOML — el seam es `Profile.from_mapping(dict)`
(design/CODEBASE-DESIGN.md §2 Seam 1). El Adapter de disco vive en
`profile/loader.py` (Fase 1c).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Mapping, Sequence

Currency = Literal["EUR", "USD", "GBP"]
WorkMode = Literal["remote", "hybrid", "onsite"]
CompanyKind = Literal["product", "consultancy", "any"]
GeoZone = Literal["ok", "onsite_local", "remote_other_eu", "onsite",
                  "us_only", "remote_unclear", "unknown"]
Verdict = Literal["APTA", "REVISAR", "DESCARTADA"]

_CURRENCIES: tuple[Currency, ...] = ("EUR", "USD", "GBP")
_WORK_MODES: tuple[WorkMode, ...] = ("remote", "hybrid", "onsite")
_COMPANY_KINDS: tuple[CompanyKind, ...] = ("product", "consultancy", "any")


class ProfileError(Exception):
    """I2: error agregado, no primero-que-falla. `.problems` es una lista de
    (ruta_de_campo, mensaje) — el wizard y `./rjs config --check` la
    imprimen tal cual."""

    def __init__(self, problems: Sequence[tuple[str, str]]):
        self.problems: tuple[tuple[str, str], ...] = tuple(problems)
        super().__init__(
            "; ".join(f"{path}: {msg}" for path, msg in self.problems)
            or "config inválida (sin detalle)")


# --- Helpers de parseo con acumulación de errores --------------------------
# Ninguno lanza: todos añaden a `problems` y devuelven un valor usable (el
# default) para que el resto de la validación pueda seguir y reportar TODOS
# los problemas de una vez (I2), no solo el primero.

def _get_dict(m: Mapping[str, object], key: str, path: str,
              problems: list[tuple[str, str]]) -> dict:
    v = m.get(key, {})
    if not isinstance(v, Mapping):
        problems.append((path, f"se esperaba un mapa, llegó {type(v).__name__}"))
        return {}
    return dict(v)


def _get_int(m: Mapping[str, object], key: str, path: str, default: int,
            problems: list[tuple[str, str]]) -> int:
    if key not in m:
        return default
    v = m[key]
    if isinstance(v, bool) or not isinstance(v, int):
        problems.append((path, f"se esperaba un entero, llegó {v!r}"))
        return default
    return v


def _get_bool(m: Mapping[str, object], key: str, path: str, default: bool,
             problems: list[tuple[str, str]]) -> bool:
    if key not in m:
        return default
    v = m[key]
    if not isinstance(v, bool):
        problems.append((path, f"se esperaba true/false, llegó {v!r}"))
        return default
    return v


def _get_str(m: Mapping[str, object], key: str, path: str, default: str,
            problems: list[tuple[str, str]]) -> str:
    if key not in m:
        return default
    v = m[key]
    if not isinstance(v, str):
        problems.append((path, f"se esperaba texto, llegó {v!r}"))
        return default
    return v


def _get_tuple_str(m: Mapping[str, object], key: str, path: str,
                   default: tuple[str, ...],
                   problems: list[tuple[str, str]]) -> tuple[str, ...]:
    if key not in m:
        return default
    v = m[key]
    if not isinstance(v, (list, tuple)):
        problems.append((path, f"se esperaba una lista, llegó {type(v).__name__}"))
        return default
    out = []
    for i, item in enumerate(v):
        if not isinstance(item, str):
            problems.append((f"{path}[{i}]", f"se esperaba texto, llegó {item!r}"))
            continue
        # Sin strip(): varias listas del clasificador original usan un
        # espacio en el borde a propósito como frontera de palabra barata
        # ("eu ", " eu", "sr ", "vp ") para no matchear dentro de otra
        # palabra ("european", "vpn"...). Recortarlo aquí los colapsaba en
        # duplicados silenciosos y rompía I7 (round-trip) — lo atrapó el
        # propio test.
        out.append(item.lower())
    return tuple(out)


def _get_currency_map(m: Mapping[str, object], key: str, path: str,
                      default: Mapping[Currency, int],
                      problems: list[tuple[str, str]]) -> dict[Currency, int]:
    if key not in m:
        return dict(default)
    raw = m[key]
    if not isinstance(raw, Mapping):
        problems.append((path, f"se esperaba un mapa moneda->cifra, llegó {raw!r}"))
        return dict(default)
    out: dict[Currency, int] = {}
    for cur, val in raw.items():
        cur_up = str(cur).upper()
        if cur_up not in _CURRENCIES:
            problems.append((f"{path}.{cur}",
                             f"moneda no soportada (usa {_CURRENCIES})"))
            continue
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            problems.append((f"{path}.{cur}", f"se esperaba un entero >= 0, llegó {val!r}"))
            continue
        out[cur_up] = val
    return out


# --- Sub-policies ------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ExperienceBand:
    ideal_min: int
    ideal_max: int
    stretch: int          # tolerable, cae en REVISAR
    max_required: int     # por encima -> DESCARTADA

    @classmethod
    def from_mapping(cls, m: Mapping[str, object], path: str,
                     problems: list[tuple[str, str]]) -> "ExperienceBand":
        ideal_min = _get_int(m, "ideal_min", f"{path}.ideal_min", 2, problems)
        ideal_max = _get_int(m, "ideal_max", f"{path}.ideal_max", 4, problems)
        stretch = _get_int(m, "stretch", f"{path}.stretch", 5, problems)
        max_required = _get_int(m, "max_required", f"{path}.max_required", 5, problems)
        # I10: orden de banda válido.
        if not (ideal_min <= ideal_max <= stretch <= max_required):
            problems.append((path,
                f"el orden debe ser ideal_min({ideal_min}) <= "
                f"ideal_max({ideal_max}) <= stretch({stretch}) <= "
                f"max_required({max_required})"))
        return cls(ideal_min, ideal_max, stretch, max_required)

    def to_mapping(self) -> dict:
        return {"ideal_min": self.ideal_min, "ideal_max": self.ideal_max,
                "stretch": self.stretch, "max_required": self.max_required}


@dataclass(frozen=True, slots=True)
class SalaryPolicy:
    floor: Mapping[Currency, int]
    warn: Mapping[Currency, int]
    floor_junior: Mapping[Currency, int]
    warn_junior: Mapping[Currency, int]
    plausible_annual: tuple[int, int] = (20_000, 600_000)

    @classmethod
    def from_mapping(cls, m: Mapping[str, object], path: str,
                     problems: list[tuple[str, str]]) -> "SalaryPolicy":
        floor = _get_currency_map(m, "floor", f"{path}.floor", {}, problems)
        warn = _get_currency_map(m, "warn", f"{path}.warn", {}, problems)
        floor_junior = _get_currency_map(m, "floor_junior", f"{path}.floor_junior",
                                         {}, problems)
        warn_junior = _get_currency_map(m, "warn_junior", f"{path}.warn_junior",
                                        {}, problems)
        plausible = m.get("plausible_annual", [20_000, 600_000])
        if (not isinstance(plausible, (list, tuple)) or len(plausible) != 2
                or not all(isinstance(x, int) and not isinstance(x, bool)
                          for x in plausible)):
            problems.append((f"{path}.plausible_annual",
                             f"se esperaban [min, max], llegó {plausible!r}"))
            plausible = (20_000, 600_000)
        # I10: warn < floor para toda moneda presente en ambos.
        for cur in set(floor) & set(warn):
            if warn[cur] >= floor[cur]:
                problems.append((f"{path}.warn.{cur}",
                    f"warn({warn[cur]}) debe ser menor que floor({floor[cur]}), "
                    "si no la franja REVISAR queda vacía o invertida"))
        for cur in set(floor_junior) & set(warn_junior):
            if warn_junior[cur] >= floor_junior[cur]:
                problems.append((f"{path}.warn_junior.{cur}",
                    f"warn_junior({warn_junior[cur]}) debe ser menor que "
                    f"floor_junior({floor_junior[cur]})"))
        return cls(floor, warn, floor_junior, warn_junior, tuple(plausible))

    def to_mapping(self) -> dict:
        return {"floor": dict(self.floor), "warn": dict(self.warn),
                "floor_junior": dict(self.floor_junior),
                "warn_junior": dict(self.warn_junior),
                "plausible_annual": list(self.plausible_annual)}


@dataclass(frozen=True, slots=True)
class StackCategory:
    weight: int
    label: str
    strong: tuple[str, ...]   # cuentan en cualquier sitio, descripción incluida
    weak: tuple[str, ...]     # SOLO cuentan en título o tags

    @classmethod
    def from_mapping(cls, m: Mapping[str, object], path: str,
                     problems: list[tuple[str, str]]) -> "StackCategory":
        weight = _get_int(m, "weight", f"{path}.weight", 1, problems)
        label = _get_str(m, "label", f"{path}.label", "", problems)
        if not label:
            problems.append((f"{path}.label", "obligatorio"))
        strong = _get_tuple_str(m, "strong", f"{path}.strong", (), problems)
        weak = _get_tuple_str(m, "weak", f"{path}.weak", (), problems)
        if not strong and not weak:
            problems.append((path, "necesita al menos una keyword en "
                             "'strong' o 'weak'; una categoría vacía nunca "
                             "puntúa"))
        return cls(weight, label, strong, weak)

    def to_mapping(self) -> dict:
        return {"weight": self.weight, "label": self.label,
                "strong": list(self.strong), "weak": list(self.weak)}


@dataclass(frozen=True, slots=True)
class StackPolicy:
    required: tuple[str, ...]              # AND duro; vacío = no filtra
    weighted: tuple[StackCategory, ...]
    min_score: int
    score_cap: int = 10
    keywords: tuple[str, ...] = ()         # antes TECH_KEYWORDS
    high_signal: tuple[str, ...] = ()      # antes HIGH_SIGNAL_KEYWORDS

    @classmethod
    def from_mapping(cls, m: Mapping[str, object], path: str,
                     problems: list[tuple[str, str]]) -> "StackPolicy":
        required = _get_tuple_str(m, "required", f"{path}.required", (), problems)
        raw_weighted = m.get("weighted", [])
        weighted: list[StackCategory] = []
        if not isinstance(raw_weighted, (list, tuple)):
            problems.append((f"{path}.weighted",
                             f"se esperaba una lista, llegó {raw_weighted!r}"))
        else:
            for i, item in enumerate(raw_weighted):
                if not isinstance(item, Mapping):
                    problems.append((f"{path}.weighted[{i}]",
                                     f"se esperaba un mapa, llegó {item!r}"))
                    continue
                weighted.append(StackCategory.from_mapping(
                    item, f"{path}.weighted[{i}]", problems))
        min_score = _get_int(m, "min_score", f"{path}.min_score", 3, problems)
        score_cap = _get_int(m, "score_cap", f"{path}.score_cap", 10, problems)
        keywords = _get_tuple_str(m, "keywords", f"{path}.keywords", (), problems)
        high_signal = _get_tuple_str(m, "high_signal", f"{path}.high_signal",
                                     (), problems)
        # I10: min_score alcanzable, si no TODO va a REVISAR sin explicación.
        max_reachable = sum(c.weight for c in weighted)
        if weighted and min_score > max_reachable:
            problems.append((f"{path}.min_score",
                f"min_score({min_score}) es mayor que la suma de todos los "
                f"pesos({max_reachable}) — ninguna oferta podría alcanzarlo"))
        return cls(required, tuple(weighted), min_score, score_cap,
                   keywords, high_signal)

    def to_mapping(self) -> dict:
        return {"required": list(self.required),
                "weighted": [c.to_mapping() for c in self.weighted],
                "min_score": self.min_score, "score_cap": self.score_cap,
                "keywords": list(self.keywords),
                "high_signal": list(self.high_signal)}


@dataclass(frozen=True, slots=True)
class GeoPolicy:
    """Sustituye SPAIN_HINTS / EU_REGION_HINTS / EU_COUNTRY_HINTS /
    NON_EU_COUNTRY_HINTS por los MISMOS cuatro roles, nombrados por rol y no
    por país (CODEBASE-DESIGN.md §5 D2): sin esto, `pais_base` es decorativo.

      home_hints       -> donde vive: vale presencial, híbrido o remoto
      region_hints     -> región amplia que CONTIENE home (p.ej. Europe/EMEA)
      away_hints       -> país concreto de la región, distinto de home
      blocked_hints    -> fuera de la región: ata la residencia, ni con remoto
      global_hints     -> promesa de alcance mundial EXPLÍCITA -> decide 'ok'
                          directamente (antes GLOBAL_LOCATION_HINTS)
      non_us_signals   -> evidencia más débil de que NO es solo-EE.UU., pero
                          SIN afirmar dónde sí vale (antes _GLOBAL_HINTS de
                          filters.py) — solo se usa dentro de is_us_only().
                          Roles distintos a propósito: fusionarlos en uno
                          solo reintroduce el bug de que "Remote - LATAM"
                          pase como 'ok' automático (visto en vivo migrando
                          esta clase — ver tests/golden).
      remote_hints     -> el puesto es remoto (no dice nada de DÓNDE)
    """
    home_hints: tuple[str, ...]
    region_hints: tuple[str, ...]
    away_hints: tuple[str, ...]
    blocked_hints: tuple[str, ...]
    global_hints: tuple[str, ...]
    remote_hints: tuple[str, ...]
    accept_modes: frozenset[WorkMode]
    accept_region_only_remote: bool = True
    non_us_signals: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, m: Mapping[str, object], path: str,
                     problems: list[tuple[str, str]]) -> "GeoPolicy":
        home = _get_tuple_str(m, "home_hints", f"{path}.home_hints", (), problems)
        region = _get_tuple_str(m, "region_hints", f"{path}.region_hints", (), problems)
        away = _get_tuple_str(m, "away_hints", f"{path}.away_hints", (), problems)
        blocked = _get_tuple_str(m, "blocked_hints", f"{path}.blocked_hints", (), problems)
        glob = _get_tuple_str(m, "global_hints", f"{path}.global_hints", (), problems)
        remote = _get_tuple_str(m, "remote_hints", f"{path}.remote_hints", (), problems)
        non_us = _get_tuple_str(m, "non_us_signals", f"{path}.non_us_signals",
                                (), problems)
        raw_modes = m.get("accept_modes", ["remote"])
        modes: set[WorkMode] = set()
        if not isinstance(raw_modes, (list, tuple)):
            problems.append((f"{path}.accept_modes",
                             f"se esperaba una lista, llegó {raw_modes!r}"))
        else:
            for mode in raw_modes:
                if mode not in _WORK_MODES:
                    problems.append((f"{path}.accept_modes",
                                     f"'{mode}' no es válido (usa {_WORK_MODES})"))
                    continue
                modes.add(mode)  # type: ignore[arg-type]
        accept_only_remote = _get_bool(m, "accept_region_only_remote",
                                       f"{path}.accept_region_only_remote",
                                       True, problems)
        # I10: un mismo hint no puede estar en home y away a la vez (la
        # ramificación de 5 vías de geo_verdict deja de ser determinista).
        overlap = set(home) & set(away)
        if overlap:
            problems.append((path, f"hints en home_hints Y away_hints a la "
                             f"vez: {sorted(overlap)}"))
        # I10: home_hints no vacío si se acepta onsite/hybrid — sin ancla no
        # hay forma de evaluar un presencial.
        if modes & {"onsite", "hybrid"} and not home:
            problems.append((f"{path}.home_hints",
                "vacío pero accept_modes incluye onsite/hybrid — sin un "
                "'donde vives' no se puede evaluar un puesto presencial"))
        return cls(home, region, away, blocked, glob, remote,
                   frozenset(modes), accept_only_remote, non_us)

    def to_mapping(self) -> dict:
        return {"home_hints": list(self.home_hints),
                "region_hints": list(self.region_hints),
                "away_hints": list(self.away_hints),
                "blocked_hints": list(self.blocked_hints),
                "global_hints": list(self.global_hints),
                "remote_hints": list(self.remote_hints),
                "accept_modes": sorted(self.accept_modes),
                "accept_region_only_remote": self.accept_region_only_remote,
                "non_us_signals": list(self.non_us_signals)}


@dataclass(frozen=True, slots=True)
class RolePolicy:
    role_signals: tuple[str, ...]
    non_role_signals: tuple[str, ...]
    management_signals: tuple[str, ...]
    senior_title_signals: tuple[str, ...]
    underqualified_signals: tuple[str, ...]
    junior_signals: tuple[str, ...]
    junior_years_range_signals: tuple[str, ...]
    mid_signals: tuple[str, ...]
    allowed_levels: frozenset[str]

    @classmethod
    def from_mapping(cls, m: Mapping[str, object], path: str,
                     problems: list[tuple[str, str]]) -> "RolePolicy":
        def lst(key: str, default=()) -> tuple[str, ...]:
            return _get_tuple_str(m, key, f"{path}.{key}", default, problems)
        allowed_raw = m.get("allowed_levels", ["junior", "mid", "unknown"])
        allowed = set()
        valid = {"junior", "mid", "senior", "unknown"}
        if not isinstance(allowed_raw, (list, tuple)):
            problems.append((f"{path}.allowed_levels",
                             f"se esperaba una lista, llegó {allowed_raw!r}"))
        else:
            for lvl in allowed_raw:
                if lvl not in valid:
                    problems.append((f"{path}.allowed_levels",
                                     f"'{lvl}' no es un nivel válido ({valid})"))
                    continue
                allowed.add(lvl)
        return cls(
            role_signals=lst("role_signals"),
            non_role_signals=lst("non_role_signals"),
            management_signals=lst("management_signals"),
            senior_title_signals=lst("senior_title_signals"),
            underqualified_signals=lst("underqualified_signals"),
            junior_signals=lst("junior_signals"),
            junior_years_range_signals=lst("junior_years_range_signals"),
            mid_signals=lst("mid_signals"),
            allowed_levels=frozenset(allowed))

    def to_mapping(self) -> dict:
        return {"role_signals": list(self.role_signals),
                "non_role_signals": list(self.non_role_signals),
                "management_signals": list(self.management_signals),
                "senior_title_signals": list(self.senior_title_signals),
                "underqualified_signals": list(self.underqualified_signals),
                "junior_signals": list(self.junior_signals),
                "junior_years_range_signals": list(self.junior_years_range_signals),
                "mid_signals": list(self.mid_signals),
                "allowed_levels": sorted(self.allowed_levels)}


@dataclass(frozen=True, slots=True)
class CompanyKindPolicy:
    kind: CompanyKind                       # "product" | "consultancy" | "any"
    consulting_signals: tuple[str, ...]
    consulting_companies: tuple[str, ...]
    staffing_platforms: tuple[str, ...]
    paywalled_domains: tuple[str, ...]

    @classmethod
    def from_mapping(cls, m: Mapping[str, object], path: str,
                     problems: list[tuple[str, str]]) -> "CompanyKindPolicy":
        kind = _get_str(m, "kind", f"{path}.kind", "any", problems)
        if kind not in _COMPANY_KINDS:
            problems.append((f"{path}.kind",
                             f"'{kind}' no es válido (usa {_COMPANY_KINDS})"))
            kind = "any"
        def lst(key: str) -> tuple[str, ...]:
            return _get_tuple_str(m, key, f"{path}.{key}", (), problems)
        return cls(kind, lst("consulting_signals"), lst("consulting_companies"),
                   lst("staffing_platforms"), lst("paywalled_domains"))

    def to_mapping(self) -> dict:
        return {"kind": self.kind,
                "consulting_signals": list(self.consulting_signals),
                "consulting_companies": list(self.consulting_companies),
                "staffing_platforms": list(self.staffing_platforms),
                "paywalled_domains": list(self.paywalled_domains)}


@dataclass(frozen=True, slots=True)
class SignalPolicy:
    positive: tuple[str, ...]
    negative: tuple[str, ...]
    strong_intl: tuple[str, ...]
    exclusivity: tuple[str, ...]

    @classmethod
    def from_mapping(cls, m: Mapping[str, object], path: str,
                     problems: list[tuple[str, str]]) -> "SignalPolicy":
        def lst(key: str) -> tuple[str, ...]:
            return _get_tuple_str(m, key, f"{path}.{key}", (), problems)
        return cls(lst("positive"), lst("negative"), lst("strong_intl"),
                   lst("exclusivity"))

    def to_mapping(self) -> dict:
        return {"positive": list(self.positive), "negative": list(self.negative),
                "strong_intl": list(self.strong_intl),
                "exclusivity": list(self.exclusivity)}


@dataclass(frozen=True, slots=True)
class SearchPolicy:
    search_terms: tuple[str, ...]
    active_sources: tuple[str, ...]
    # Only for the opt-in LinkedIn / Indeed sources: full-text queries and the
    # locations those boards search in (e.g. "European Union", "Spain").
    board_queries: tuple[str, ...] = ()
    board_locations: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, m: Mapping[str, object], path: str,
                     problems: list[tuple[str, str]]) -> "SearchPolicy":
        search_terms = _get_tuple_str(m, "search_terms", f"{path}.search_terms",
                                      (), problems)
        active_sources = _get_tuple_str(m, "active_sources",
                                        f"{path}.active_sources", (), problems)
        board_queries = _get_tuple_str(m, "board_queries",
                                       f"{path}.board_queries", (), problems)
        board_locations = _get_tuple_str(m, "board_locations",
                                         f"{path}.board_locations", (), problems)
        return cls(search_terms, active_sources, board_queries, board_locations)

    def to_mapping(self) -> dict:
        return {"search_terms": list(self.search_terms),
                "active_sources": list(self.active_sources),
                "board_queries": list(self.board_queries),
                "board_locations": list(self.board_locations)}


# --- Profile: el agregado ----------------------------------------------------

@dataclass(frozen=True, slots=True)
class Profile:
    experience: ExperienceBand
    salary: SalaryPolicy
    stack: StackPolicy
    geo: GeoPolicy
    role: RolePolicy
    company: CompanyKindPolicy
    signals: SignalPolicy
    narrative: str            # I6: contents of profile.md, ALREADY READ
    search: SearchPolicy

    # --- Interface pública ---------------------------------------------

    @classmethod
    def defaults(cls) -> "Profile":
        """I8: los defaults viven en código, no en un fichero. Tiene que
        funcionar con el disco vacío (clone recién hecho, CI, tests)."""
        from . import defaults as _defaults
        return _defaults.PROFILE_DEFAULTS

    @classmethod
    def from_mapping(cls, m: Mapping[str, object], *,
                     strict: bool = True) -> "Profile":
        """I1: devuelve un Profile completamente válido o lanza ProfileError.
        I3: con strict=True (default), una clave desconocida en el nivel
        raíz es error — una errata de tecleo no debe revertir en silencio a
        un default sin que el usuario se entere."""
        problems: list[tuple[str, str]] = []
        known_top = {"experience", "salary", "stack", "geo", "role",
                    "company", "signals", "narrative", "search"}
        if strict:
            for key in m:
                if key not in known_top:
                    problems.append((key, "clave desconocida en el nivel raíz"))

        experience = ExperienceBand.from_mapping(
            _get_dict(m, "experience", "experience", problems),
            "experience", problems)
        salary = SalaryPolicy.from_mapping(
            _get_dict(m, "salary", "salary", problems), "salary", problems)
        stack = StackPolicy.from_mapping(
            _get_dict(m, "stack", "stack", problems), "stack", problems)
        geo = GeoPolicy.from_mapping(
            _get_dict(m, "geo", "geo", problems), "geo", problems)
        role = RolePolicy.from_mapping(
            _get_dict(m, "role", "role", problems), "role", problems)
        company = CompanyKindPolicy.from_mapping(
            _get_dict(m, "company", "company", problems), "company", problems)
        signals = SignalPolicy.from_mapping(
            _get_dict(m, "signals", "signals", problems), "signals", problems)
        narrative = _get_str(m, "narrative", "narrative", "", problems)
        search = SearchPolicy.from_mapping(
            _get_dict(m, "search", "search", problems), "search", problems)

        if problems:
            raise ProfileError(problems)
        return cls(experience, salary, stack, geo, role, company, signals,
                   narrative, search)

    def to_mapping(self) -> dict[str, object]:
        return {
            "experience": self.experience.to_mapping(),
            "salary": self.salary.to_mapping(),
            "stack": self.stack.to_mapping(),
            "geo": self.geo.to_mapping(),
            "role": self.role.to_mapping(),
            "company": self.company.to_mapping(),
            "signals": self.signals.to_mapping(),
            "narrative": self.narrative,
            "search": self.search.to_mapping(),
        }

    def thesis(self) -> str:
        """I11: derivada, no un campo. Interpola los umbrales reales sobre
        `narrative` para que el juez IA y el filtro determinista no puedan
        discrepar — hoy sí pueden: USER_THESIS dice '40k€' en prosa mientras
        MIN_SALARY lo dice en un dict, y si uno cambia el otro no se entera."""
        floor = self.salary.floor
        floor_txt = ", ".join(f"{v:,}{c}" for c, v in sorted(floor.items()))
        lo, hi = self.experience.ideal_min, self.experience.ideal_max
        return (
            f"{self.narrative}\n\n"
            f"[Active thresholds - do not contradict them when scoring: "
            f"salary floor {floor_txt or 'not set'}; "
            f"ideal experience {lo}-{hi} years, tolerable up to "
            f"{self.experience.stretch}, rejected above "
            f"{self.experience.max_required}; "
            f"minimum stack score {self.stack.min_score}/{self.stack.score_cap}.]"
        )


def replace_in(profile: Profile, **changes) -> Profile:
    """Azúcar sobre dataclasses.replace para el caso común de un solo campo
    de primer nivel — el resto de mutaciones usan replace() anidado
    directamente (I4: Profile es inmutable, no hay setters)."""
    return replace(profile, **changes)
