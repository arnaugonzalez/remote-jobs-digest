"""Esquema normalizado de oferta + utilidades HTTP compartidas.

Cada fuente (remoteok, remotive, ...) traduce su JSON/RSS propio a este `Job`
común. A partir de aquí, filtrado y exportación no saben de qué fuente vino.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import httpx

from remote_jobs_digest import config


@dataclass
class Job:
    source: str
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    posted_date: str = ""            # ISO-8601 si se conoce
    employment_type: str = ""        # Full-time / Contract / ...
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: str = ""
    salary_text: str = ""            # texto crudo del salario, si lo hay
    tags: list[str] = field(default_factory=list)
    easy_apply: bool = False         # LinkedIn Easy Apply (respuesta rápida)
    # Campos rellenados por filters.py:
    verdict: str = ""                # APTA / REVISAR / DESCARTADA
    reason: str = ""
    relevance: int = 0               # nº de keywords de stack que matchean
    is_relevant: bool = True
    sort_salary: int = 0             # clave de orden (0 = sin salario)
    # junior/mid/senior/unknown + los cortes overqualified/underqualified
    level: str = ""
    stack_score: int = 0             # 0-10 ponderado (config.STACK_WEIGHTS)
    stack_hits: list[str] = field(default_factory=list)
    salary_flag: str = ""            # ok / low / below_floor / unknown
    geo: str = ""                    # ok / us_only / onsite / unknown
    years_required: Optional[int] = None   # años que pide el anuncio
    is_ai_role: bool = False         # el stack toca AI/LLM de verdad
    is_new: bool = True              # no vista en runs anteriores (seen_ids)
    has_exclusivity: bool = False    # cláusula que choca con el freelance
    is_consulting: bool = False      # consultoría/body-shopping
    # Campos rellenados por ai_filter.py (opcional, vía Groq):
    ai_level: str = ""               # junior / mid / senior (juicio del LLM)
    ai_fit: int | None = None        # 0-10: encaje con la tesis del usuario
    ai_workload: str = ""            # relaxed / moderate / intense / unknown
    ai_reason: str = ""              # una línea de justificación
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def id(self) -> str:
        # id estable por fuente+url para deduplicar entre días.
        return f"{self.source}:{self.url}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        return d


def log(msg: str) -> None:
    """Log a stderr para no contaminar stdout (que puede ser parseado)."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def http_get(url: str, *, params: dict | None = None,
             headers: dict | None = None, retries: int = 2) -> httpx.Response:
    """GET con timeout, headers por defecto y reintentos simples."""
    last_exc: Exception | None = None
    merged = {**config.HTTP_HEADERS, **(headers or {})}
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=config.HTTP_TIMEOUT,
                              follow_redirects=True) as client:
                r = client.get(url, params=params, headers=merged)
                r.raise_for_status()
                return r
        except Exception as exc:  # noqa: BLE001 — resiliencia deliberada
            last_exc = exc
            log(f"  attempt {attempt + 1}/{retries + 1} failed for {url}: {exc}")
    raise last_exc  # type: ignore[misc]
