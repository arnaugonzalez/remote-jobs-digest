"""Fuente We Work Remotely — feeds RSS públicos (sin auth).

WWR no tiene API JSON, pero cada categoría expone un RSS estable. El salario y la
región suelen ir embebidos en el título/descripcion, así que filters.py los
extrae por regex. El título RSS típico es "Company: Job Title".
"""

from __future__ import annotations

import html
import re

import feedparser

from .base import Job, http_get, log

RSS_FEEDS = {
    "programming": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "devops": "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "fullstack": "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "backend": "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
}

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", text or "")).strip()


def _split_title(raw: str) -> tuple[str, str]:
    """'Acme Inc: Senior Python Dev' -> (company, title)."""
    if ":" in raw:
        company, _, title = raw.partition(":")
        return company.strip(), title.strip()
    return "", raw.strip()


def fetch() -> list[Job]:
    seen: set[str] = set()
    jobs: list[Job] = []
    for category, url in RSS_FEEDS.items():
        try:
            # Descargamos con nuestro http_get (UA propio) y dejamos que
            # feedparser parsee el contenido — evita bloqueos por UA por defecto.
            content = http_get(url).text
            feed = feedparser.parse(content)
        except Exception as exc:  # noqa: BLE001
            log(f"  wwr {category} failed: {exc}")
            continue
        for entry in feed.entries:
            link = entry.get("link", "")
            if not link or link in seen:
                continue
            seen.add(link)
            company, title = _split_title(entry.get("title", ""))
            # WWR mete la región en una <category> del item.
            region = ""
            for tag in entry.get("tags", []) or []:
                term = tag.get("term", "")
                if term and any(k in term.lower() for k in
                                ("anywhere", "americas", "europe", "world",
                                 "remote", "usa", "emea")):
                    region = term
                    break
            jobs.append(Job(
                source="weworkremotely",
                title=title,
                company=company,
                url=link,
                location=region or "Remote",
                description=_strip_html(entry.get("summary", "")),
                posted_date=entry.get("published", "") or "",
                employment_type="",
                tags=[category],
            ))
    log(f"weworkremotely: {len(jobs)} jobs")
    return jobs
