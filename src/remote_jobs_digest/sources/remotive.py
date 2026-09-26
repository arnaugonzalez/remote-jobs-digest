"""Fuente Remotive — API JSON pública (sin auth).

GET https://remotive.com/api/remote-jobs?search=TERM  ->  {"jobs": [...]}.
El salario viene como texto libre ("$120k - $150k", "60000 USD"...), así que se
parsea luego en filters.py. `candidate_required_location` es clave para la
aptitud internacional ("Worldwide", "USA Only", "Europe"...).
"""

from __future__ import annotations

from remote_jobs_digest import config
from .base import Job, http_get, log

API_URL = "https://remotive.com/api/remote-jobs"


def _fetch_term(term: str | None) -> list[dict]:
    params = {"limit": 60}
    if term:
        params["search"] = term
    r = http_get(API_URL, params=params)
    return r.json().get("jobs", []) or []


def fetch() -> list[Job]:
    seen: set[str] = set()
    jobs: list[Job] = []
    # Una pasada general + una por término de búsqueda del stack.
    terms: list[str | None] = [None] + config.SEARCH_TERMS
    for term in terms:
        try:
            raw = _fetch_term(term)
        except Exception as exc:  # noqa: BLE001
            log(f"  remotive term={term!r} failed: {exc}")
            continue
        for item in raw:
            url = item.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            loc = item.get("candidate_required_location", "") or ""
            jobs.append(Job(
                source="remotive",
                title=(item.get("title") or "").strip(),
                company=(item.get("company_name") or "").strip(),
                url=url,
                location=loc.strip() or "Remote",
                description=item.get("description", "") or "",
                posted_date=item.get("publication_date", "") or "",
                employment_type=item.get("job_type", "") or "",
                salary_text=(item.get("salary") or "").strip(),
                tags=[t for t in (item.get("tags") or []) if isinstance(t, str)],
            ))
    log(f"remotive: {len(jobs)} jobs")
    return jobs
