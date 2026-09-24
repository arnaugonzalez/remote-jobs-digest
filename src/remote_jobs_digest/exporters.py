"""Exportación a JSON (crudo) y CSV (filtrado y ordenado por salario)."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime

from remote_jobs_digest import config
from remote_jobs_digest.classifier import Classifier
from remote_jobs_digest.profile.loader import load_profile
from remote_jobs_digest.sources.base import Job

# design/CODEBASE-DESIGN.md §5 D6: MIN_STACK_SCORE/IDEAL_YEARS son JUICIO,
# no datos — viven detrás de la Interface del Classifier
# (meets_stack_floor/in_ideal_years), no como umbrales que cada consumidor
# vuelve a comparar por su cuenta.
_CLF = Classifier(load_profile())

CSV_COLUMNS = [
    "verdict", "is_new", "easy_apply", "is_ai_role", "years_required",
    "stack_score", "stack_hits",
    "level", "geo", "ai_level", "ai_fit", "ai_workload", "title", "company",
    "salary_flag", "salary_text",
    "salary_min", "salary_max", "salary_currency", "has_exclusivity", "location", "source",
    "relevance", "ai_reason", "url", "reason", "posted_date",
]


def _passes_base(job: Job) -> bool:
    """Filtros deterministas comunes (sin IA): stack con señal, apta, nivel IC.

    Ojo: el gate es stack_score, no is_relevant. is_relevant mira solo el
    título, y con la fuente 'ats' muchas ofertas se llaman "Software Engineer"
    a secas y piden FastAPI en el cuerpo — el título deja de ser fiable.
    """
    return (job.verdict in config.DIGEST_VERDICTS
            and job.level in config.ALLOWED_LEVELS
            and _CLF.meets_stack_floor(job))


def pre_ai_candidates(jobs: list[Job]) -> list[Job]:
    """Ofertas que merecen gastar una llamada de IA, las mejores primero."""
    cands = [j for j in jobs if _passes_base(j)]
    cands.sort(key=lambda j: (-j.stack_score, -(j.sort_salary or 0)))
    return cands


def filter_and_sort(jobs: list[Job], ai_used: bool = False) -> list[Job]:
    keep = []
    for j in jobs:
        if not _passes_base(j):
            continue
        if ai_used:
            # 'senior' ya no descarta: el corte lo hace detect_level con
            # OVERQUALIFIED_SIGNALS. Aquí solo filtramos por encaje con la tesis.
            if j.ai_fit is not None and j.ai_fit < config.AI_MIN_FIT:
                continue
        keep.append(j)
    # El objetivo es un rol de AI engineering: los que lo son van primero,
    # y dentro de ellos los que piden 2-4 años (el punto dulce del perfil).
    def _ideal_years(j: Job) -> int:
        if j.years_required is None:
            return 1                      # sin dato: entre medias
        return 0 if _CLF.in_ideal_years(j) else 2

    # Lo NUEVO primero: el digest es diario y su valor está en lo que apareció
    # desde ayer, no en repetir las mismas 12 de siempre.
    # easy_apply desempata por delante del stack (nunca por delante del encaje):
    # a igual calidad, la oferta donde se puede aplicar en 1 clic va antes.
    if ai_used:
        keep.sort(key=lambda j: (not j.is_new, not j.is_ai_role,
                                 _ideal_years(j),
                                 -(j.ai_fit if j.ai_fit is not None else -1),
                                 not j.easy_apply,
                                 -j.stack_score, -(j.sort_salary or 0)))
    else:
        keep.sort(key=lambda j: (not j.is_new, not j.is_ai_role,
                                 _ideal_years(j),
                                 not j.easy_apply,
                                 -j.stack_score, -(j.sort_salary or 0)))
    return keep


def save_results(all_jobs: list[Job], filtered: list[Job]) -> dict:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    raw_path = os.path.join(config.OUTPUT_DIR, f"jobs_raw_{ts}.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump([j.to_dict() for j in all_jobs], f,
                  indent=2, ensure_ascii=False, default=str)

    csv_path = os.path.join(config.OUTPUT_DIR, f"jobs_filtered_{ts}.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for j in filtered:
            writer.writerow(j.to_dict())

    # "latest" estable para que el cron/digest siempre apunte al último.
    latest_csv = os.path.join(config.OUTPUT_DIR, "jobs_filtered_latest.csv")
    latest_json = os.path.join(config.OUTPUT_DIR, "jobs_raw_latest.json")
    for src, dst in [(csv_path, latest_csv), (raw_path, latest_json)]:
        with open(src, "rb") as a, open(dst, "wb") as b:
            b.write(a.read())

    apta = sum(1 for j in all_jobs if j.verdict == "APTA")
    revisar = sum(1 for j in all_jobs if j.verdict == "REVISAR")
    descart = sum(1 for j in all_jobs if j.verdict == "DESCARTADA")
    # Descartadas por ser gestión/staff+ pese a encajar en stack: si este número
    # se dispara, el mercado te está empujando a leadership y conviene saberlo.
    mgmt_dropped = sum(1 for j in all_jobs
                       if _CLF.meets_stack_floor(j)
                       and j.level == "overqualified")
    consulting_dropped = sum(1 for j in all_jobs
                             if _CLF.meets_stack_floor(j)
                             and j.is_consulting)
    exclusivity = sum(1 for j in all_jobs if j.has_exclusivity)

    return {
        "raw_path": raw_path, "csv_path": csv_path,
        "total": len(all_jobs), "filtered": len(filtered),
        "new_filtered": sum(1 for j in filtered if j.is_new),
        "apta": apta, "revisar": revisar, "descartada": descart,
        "mgmt_dropped": mgmt_dropped, "consulting_dropped": consulting_dropped,
        "exclusivity": exclusivity,
    }
