"""Fuente Hacker News — hilo mensual "Ask HN: Who is hiring?".

Sale el día 1 de cada mes y concentra mucha startup remota que nunca aparece en
los agregadores. La API de Algolia es pública y sin auth:

    hn.algolia.com/api/v1/search?query=...&tags=story      -> encuentra el hilo
    hn.algolia.com/api/v1/items/{id}                        -> comentarios

Cada comentario de primer nivel es una oferta, con un formato convencional pero
no obligatorio:

    Empresa | Puesto | Ubicación | REMOTE | tecnologías | contacto/enlace

Se parsea lo que se puede y el resto va al cuerpo, que es lo que lee el
clasificador. Las ofertas sin enlace aplicable se descartan luego por no tener
URL utilizable.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from .base import Job, http_get, log

# search_by_date, no search: el endpoint por relevancia devolvía el hilo de
# 2020 como primer resultado — ofertas caducadas hace años.
SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL = "https://hn.algolia.com/api/v1/items/{id}"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Primer enlace del comentario: casi siempre el board de la empresa.
_HREF_RE = re.compile(r'href="([^"]+)"')
# Separadores típicos de la primera línea: "Empresa | Puesto | Remote | ..."
_SPLIT_RE = re.compile(r"\s*[|•·—–]\s*")


def _clean(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", raw))
    return _WS_RE.sub(" ", text).strip()


def _find_thread_id() -> int | None:
    """Hilo 'Who is hiring?' más reciente publicado por whoishiring."""
    try:
        r = http_get(SEARCH_URL, params={
            "query": "Who is hiring",
            "tags": "story,author_whoishiring",
            "hitsPerPage": 20,
        })
        hits = r.json().get("hits", []) or []
    except Exception as exc:  # noqa: BLE001
        log(f"  hn: search failed: {exc}")
        return None
    best, best_ts = None, 0
    for h in hits:
        title = (h.get("title") or "").lower()
        if "who is hiring" not in title or "who wants to be hired" in title:
            continue
        ts = h.get("created_at_i") or 0
        if ts > best_ts:
            best, best_ts = h.get("objectID"), ts
    if not best:
        return None
    when = datetime.fromtimestamp(best_ts, timezone.utc).strftime("%Y-%m")
    log(f"  hn: thread {best} ({when})")
    return int(best)


def _best_url(raw_html: str) -> str:
    """Primer enlace que parezca un board de empresa, si lo hay."""
    urls = _HREF_RE.findall(raw_html or "")
    if not urls:
        return ""
    prefer = ("greenhouse", "lever.co", "ashbyhq", "workable", "recruitee",
              "smartrecruiters", "breezy", "personio", "careers", "/jobs")
    for u in urls:
        if any(p in u.lower() for p in prefer):
            return html.unescape(u)
    return html.unescape(urls[0])


def fetch() -> list[Job]:
    thread_id = _find_thread_id()
    if not thread_id:
        log("hackernews: monthly thread not found")
        return []
    try:
        r = http_get(ITEM_URL.format(id=thread_id))
        children = r.json().get("children", []) or []
    except Exception as exc:  # noqa: BLE001
        log(f"hackernews: could not read the thread: {exc}")
        return []

    jobs: list[Job] = []
    for c in children:
        raw = c.get("text") or ""
        if not raw or c.get("author") == "whoishiring":
            continue
        text = _clean(raw)
        if len(text) < 40:
            continue
        # La primera línea suele traer empresa | puesto | ubicación.
        head = text[:220]
        parts = [p.strip() for p in _SPLIT_RE.split(head) if p.strip()]
        company = parts[0][:70] if parts else (c.get("author") or "HN")
        title = parts[1][:120] if len(parts) > 1 else "(ver descripción)"
        location = " ".join(parts[2:4])[:90] if len(parts) > 2 else ""
        if "remote" in head.lower() and "remote" not in location.lower():
            location = (location + " Remote").strip()
        url = _best_url(raw) or f"https://news.ycombinator.com/item?id={c.get('id')}"
        jobs.append(Job(
            source="hackernews", title=title, company=company, url=url,
            location=location or "Remote", description=text[:4000],
            posted_date=c.get("created_at", "") or "",
            tags=["hn-who-is-hiring"]))
    log(f"hackernews: {len(jobs)} jobs from the monthly thread")
    return jobs
