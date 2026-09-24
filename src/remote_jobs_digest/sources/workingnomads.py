"""Fuente Working Nomads — endpoint JSON no documentado oficialmente (sin auth).

GET https://www.workingnomads.com/api/exposed_jobs/  ->  [{...}, ...]

Ventana rodante de ~1 mes (no hay paginación real: /?page=2 devuelve lo
mismo), así que el dedup por seen_ids del scraper es lo que evita que se
repita. `description` viene con HTML embebido.
"""

from __future__ import annotations

import html
import re

from .base import Job, http_get, log

API_URL = "https://www.workingnomads.com/api/exposed_jobs/"

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(raw: str | None) -> str:
    if not raw:
        return ""
    return html.unescape(_TAG_RE.sub(" ", raw)).strip()


def fetch() -> list[Job]:
    try:
        raw = http_get(API_URL).json()
    except Exception as exc:  # noqa: BLE001
        log(f"  workingnomads falló: {exc}")
        return []
    jobs: list[Job] = []
    for item in raw or []:
        url = item.get("url", "")
        if not url:
            continue
        tags_raw = item.get("tags", "") or ""
        jobs.append(Job(
            source="workingnomads",
            title=(item.get("title") or "").strip(),
            company=(item.get("company_name") or "").strip(),
            url=url,
            location=(item.get("location") or "").strip() or "Remote",
            description=_clean(item.get("description")),
            posted_date=item.get("pub_date", "") or "",
            tags=[t.strip() for t in tags_raw.split(",") if t.strip()],
        ))
    log(f"workingnomads: {len(jobs)} ofertas")
    return jobs
