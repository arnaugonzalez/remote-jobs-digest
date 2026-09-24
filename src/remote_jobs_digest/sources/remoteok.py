"""Fuente RemoteOK — API JSON pública (sin auth).

GET https://remoteok.com/api  ->  lista JSON. El primer elemento es metadata
legal (hay que saltarlo). Cada oferta trae salary_min/salary_max en USD, lo que
la hace ideal para el orden por salario.
"""

from __future__ import annotations

from .base import Job, http_get, log

API_URL = "https://remoteok.com/api"


def _to_int(v) -> int | None:
    try:
        n = int(v)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def fetch() -> list[Job]:
    r = http_get(API_URL)
    data = r.json()
    jobs: list[Job] = []
    for item in data:
        # El elemento de metadata legal no tiene "position".
        if not isinstance(item, dict) or not item.get("position"):
            continue
        smin = _to_int(item.get("salary_min"))
        smax = _to_int(item.get("salary_max"))
        jobs.append(Job(
            source="remoteok",
            title=item.get("position", "").strip(),
            company=(item.get("company") or "").strip(),
            url=item.get("url") or f"https://remoteok.com/l/{item.get('id', '')}",
            location=(item.get("location") or "Remote").strip(),
            description=item.get("description", "") or "",
            posted_date=item.get("date", "") or "",
            employment_type="",  # RemoteOK no lo expone de forma fiable
            salary_min=smin,
            salary_max=smax,
            salary_currency="USD" if (smin or smax) else "",
            salary_text=(f"${smin:,}–${smax:,}" if smin and smax else ""),
            tags=[t for t in (item.get("tags") or []) if isinstance(t, str)],
        ))
    log(f"remoteok: {len(jobs)} ofertas")
    return jobs
