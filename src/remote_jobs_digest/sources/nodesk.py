"""Fuente NoDesk — RSS público (sin auth).

GET https://nodesk.co/remote-jobs/index.xml

El título mezcla dos formatos según la oferta:
    "Título | Ubicación | Remote at Empresa"
    "Título at Empresa"                        (sin segmento de ubicación)
Se separa por el ÚLTIMO " at " (nombres de empresa no lo llevan) y lo que
queda antes se trocea por "|" para sacar título + ubicación.
"""

from __future__ import annotations

import html
import re

import feedparser

from .base import Job, http_get, log

RSS_URL = "https://nodesk.co/remote-jobs/index.xml"

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", text or "")).strip()


def _parse_title(raw: str) -> tuple[str, str, str]:
    if " at " in raw:
        head, _, company = raw.rpartition(" at ")
    else:
        head, company = raw, ""
    segments = [s.strip() for s in head.split("|") if s.strip()]
    title = segments[0] if segments else head.strip()
    location = ", ".join(segments[1:])
    return title, location, company.strip()


def fetch() -> list[Job]:
    try:
        content = http_get(RSS_URL).text
        feed = feedparser.parse(content)
    except Exception as exc:  # noqa: BLE001
        log(f"  nodesk falló: {exc}")
        return []
    jobs: list[Job] = []
    for entry in feed.entries:
        link = entry.get("link", "")
        if not link:
            continue
        title, location, company = _parse_title(entry.get("title", ""))
        jobs.append(Job(
            source="nodesk",
            title=title,
            company=company,
            url=link,
            location=location or "Remote",
            description=_strip_html(entry.get("summary", "")),
            posted_date=entry.get("published", "") or "",
        ))
    log(f"nodesk: {len(jobs)} ofertas")
    return jobs
