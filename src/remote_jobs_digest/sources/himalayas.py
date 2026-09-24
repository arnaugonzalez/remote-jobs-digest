"""Fuente Himalayas — API JSON pública (sin auth).

GET https://himalayas.app/jobs/api?limit=N  ->  {"jobs": [...]}.
Expone minSalary/maxSalary numéricos y `locationRestrictions` (lista de países/
regiones), lo que da una señal muy fiable de aptitud internacional.
Se programa defensivamente porque Himalayas ha cambiado nombres de campos.
"""

from __future__ import annotations

from .base import Job, http_get, log

API_URL = "https://himalayas.app/jobs/api"
# Sin estos headers el endpoint responde 403. Contraintuitivo: el UA Chrome
# "completo" de config dispara la protección anti-bot (falta sec-ch-ua, lo que
# delata el spoof); un UA simple pasa. Por eso lo sobrescribimos aquí.
HEADERS = {
    "Accept": "application/json",
    "Referer": "https://himalayas.app/jobs",
    "User-Agent": "Mozilla/5.0",
}


def _to_int(v) -> int | None:
    try:
        n = int(float(v))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _first(item: dict, *keys, default=""):
    for k in keys:
        if item.get(k):
            return item[k]
    return default


def fetch() -> list[Job]:
    r = http_get(API_URL, params={"limit": 100}, headers=HEADERS)
    payload = r.json()
    raw = payload.get("jobs", payload if isinstance(payload, list) else []) or []
    jobs: list[Job] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = _first(item, "title", "position")
        if not title:
            continue
        restrictions = item.get("locationRestrictions") or item.get("locations") or []
        if isinstance(restrictions, str):
            restrictions = [restrictions]
        location = ", ".join(str(x) for x in restrictions) or "Remote"
        smin = _to_int(_first(item, "minSalary", "salaryMin", default=None))
        smax = _to_int(_first(item, "maxSalary", "salaryMax", default=None))
        currency = _first(item, "salaryCurrency", "currency", default="USD")
        url = _first(item, "applicationLink", "guid", "url")
        if not url:
            slug = _first(item, "slug")
            url = f"https://himalayas.app/companies/jobs/{slug}" if slug else ""
        if not url:
            continue
        cats = item.get("categories") or item.get("tags") or []
        jobs.append(Job(
            source="himalayas",
            title=str(title).strip(),
            company=str(_first(item, "companyName", "company")).strip(),
            url=url,
            location=location,
            description=_first(item, "description", "excerpt"),
            posted_date=str(_first(item, "pubDate", "publishedAt", "date")),
            employment_type=str(_first(item, "employmentType", "seniority")),
            salary_min=smin,
            salary_max=smax,
            salary_currency=currency if (smin or smax) else "",
            salary_text=(f"{smin:,}–{smax:,} {currency}" if smin and smax else ""),
            tags=[str(c) for c in cats if c],
        ))
    log(f"himalayas: {len(jobs)} ofertas")
    return jobs
