"""Fuentes vía python-jobspy: Indeed, Glassdoor y Google Jobs.

JobSpy (github.com/speedyapply/JobSpy) mantiene los scrapers de estos boards
al día mejor de lo que podríamos hacerlo a mano — Indeed en particular no
aplica rate limit y devuelve la descripción completa, que filters.py necesita
para years_required y el stack score fuerte.

LinkedIn NO va por aquí a propósito: el filtro easy_apply de JobSpy para
LinkedIn ya no funciona (lo admite su propia documentación), así que LinkedIn
tiene fuente propia (sources/linkedin.py) contra el endpoint guest con f_AL.

La dependencia es opcional: si python-jobspy no está instalado, la fuente se
omite con un aviso en vez de romper el run (mismo patrón que weworkremotely
con feedparser).
"""

from __future__ import annotations

from remote_jobs_digest import config
from .base import Job, log


def _s(value) -> str:
    """str segura para celdas de DataFrame: None/NaN -> ''."""
    if value is None or value != value:   # NaN != NaN
        return ""
    return str(value).strip()


def _row_to_job(site: str, row: dict) -> Job | None:
    url = _s(row.get("job_url"))
    title = _s(row.get("title"))
    if not url or not title:
        return None
    job = Job(
        source=site,
        title=title,
        company=_s(row.get("company")),
        url=url,
        location=_s(row.get("location")) or "Remote",
        description=_s(row.get("description")),
        posted_date=_s(row.get("date_posted")),
        employment_type=_s(row.get("job_type")),
    )
    lo, hi = row.get("min_amount"), row.get("max_amount")
    currency = _s(row.get("currency")) or "USD"
    interval = _s(row.get("interval"))
    if lo == lo and lo and interval == "yearly":     # NaN check
        job.salary_min = int(lo)
        job.salary_max = int(hi) if hi == hi and hi else int(lo)
        job.salary_currency = currency
    elif lo == lo and lo:
        # Tarifa por hora/día: se deja como texto; el parser de filters.py
        # detecta '/hr' y no lo confunde con un salario anual.
        job.salary_text = f"{lo}-{_s(hi) or lo} {currency}/hr ({interval})"
    return job


def _fetch_site(site: str) -> list[Job]:
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log(f"  {site}: python-jobspy no instalado "
            f"(pip install python-jobspy); fuente omitida")
        return []

    jobs: list[Job] = []
    seen: set[str] = set()
    for country in config.JOBSPY_COUNTRIES:
        for term in config.JOBSPY_SEARCH_TERMS:
            try:
                df = scrape_jobs(
                    site_name=[site],
                    search_term=term,
                    google_search_term=f"{term} remote jobs",
                    is_remote=True,
                    hours_old=config.JOBSPY_HOURS,
                    results_wanted=config.JOBSPY_RESULTS,
                    country_indeed=country,
                    verbose=0,
                )
            except Exception as exc:  # noqa: BLE001 — una query caída no mata la fuente
                log(f"  {site} q={term!r} país={country}: {exc}")
                continue
            for row in df.to_dict("records"):
                job = _row_to_job(site, row)
                if job and job.url not in seen:
                    seen.add(job.url)
                    jobs.append(job)
    log(f"{site}: {len(jobs)} ofertas")
    return jobs


def fetch_indeed() -> list[Job]:
    return _fetch_site("indeed")


def fetch_glassdoor() -> list[Job]:
    return _fetch_site("glassdoor")


def fetch_google() -> list[Job]:
    return _fetch_site("google")
