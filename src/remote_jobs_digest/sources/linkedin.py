"""Fuente LinkedIn — endpoint guest de búsqueda (sin login).

LinkedIn expone la misma búsqueda que ve un visitante sin sesión en
GET https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
Devuelve un fragmento HTML con ~10 tarjetas <li> por página (title/company/
location/url y, a veces, salario). Acepta los filtros de la UI como params:

    f_AL=true     -> solo Easy Apply (aplicación en 1 clic, la de mejor
                     tasa de respuesta según la experiencia de uso)
    f_WT=2        -> solo remoto
    f_TPR=rNNNN   -> publicadas en los últimos NNNN segundos
    start=N       -> paginación; se incrementa por tarjetas RECIBIDAS
                     (la página real es de 10, no de 25)

Se hacen dos pasadas por (término, ubicación): primero Easy Apply (marca
`easy_apply=True`) y luego general.

La tarjeta NO trae descripción. Se pide aparte a
/jobs-guest/jobs/api/jobPosting/{id}, que además trae los criterios del
anuncio (Seniority level, Employment type) y a veces bloque de salario.
La descripción es la que alimenta years_required (en España suele venir en
español: "3 años de experiencia"), el stack score fuerte y las señales geo,
así que el presupuesto de requests de detalle se gasta con criterio:
Easy Apply y títulos de ingeniería primero, gestión ni se pide.

Rate limit: LinkedIn corta con 429/999 si se abusa. Mitigación: pocas queries
de alta señal, delay entre requests y corte de la fuente tras 3 errores
consecutivos (lo ya recolectado se devuelve igualmente).
"""

from __future__ import annotations

import html
import re
import time

from remote_jobs_digest import config
from .base import Job, http_get, log

SEARCH_URL = ("https://www.linkedin.com/jobs-guest/jobs/api/"
              "seeMoreJobPostings/search")
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

_CARD_RE = re.compile(r"<li>(.+?)</li>", re.S)
_LINK_RE = re.compile(r'href="(https://[^"]*?/jobs/view/[^"]+?)"')
_TITLE_RE = re.compile(r"<h3[^>]*base-search-card__title[^>]*>(.*?)</h3>", re.S)
_COMPANY_RE = re.compile(r"<h4[^>]*base-search-card__subtitle[^>]*>(.*?)</h4>",
                         re.S)
_LOCATION_RE = re.compile(
    r"<span[^>]*job-search-card__location[^>]*>(.*?)</span>", re.S)
_CARD_SALARY_RE = re.compile(
    r"<span[^>]*salary-info[^>]*>(.*?)</span>", re.S)
_TIME_RE = re.compile(r'datetime="(\d{4}-\d{2}-\d{2})"')
_JOB_ID_RE = re.compile(r"-(\d{7,})(?:\?|$)")
_DESC_RE = re.compile(r"show-more-less-html__markup[^>]*>(.*?)</div>", re.S)
_DETAIL_SALARY_RE = re.compile(r"compensation__salary[^>]*>(.*?)</div>", re.S)
_CRITERIA_RE = re.compile(
    r"description__job-criteria-subheader[^>]*>(.*?)</h3>\s*"
    r"<span[^>]*description__job-criteria-text[^>]*>(.*?)</span>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")

# Los headers de navegador de config.HTTP_HEADERS ya bastan; solo fijamos
# idioma para que las ubicaciones vengan en inglés (las listas geo lo esperan).
_HEADERS = {"Accept-Language": "en-US,en;q=0.9"}


def _strip(fragment: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _parse_cards(html_fragment: str, *, easy: bool) -> list[Job]:
    jobs: list[Job] = []
    for card in _CARD_RE.findall(html_fragment):
        link = _LINK_RE.search(card)
        title = _TITLE_RE.search(card)
        if not link or not title:
            continue
        url = link.group(1).split("?", 1)[0]
        company = _COMPANY_RE.search(card)
        location = _LOCATION_RE.search(card)
        salary = _CARD_SALARY_RE.search(card)
        posted = _TIME_RE.search(card)
        # La búsqueda entera lleva f_WT=2: TODO lo que devuelve es remoto,
        # pero la tarjeta pone solo la ciudad ("Athens, Greece"). Sin el
        # prefijo, el clasificador geo lo lee como presencial en el
        # extranjero y lo tira por "implica mudarse" en vez de dejarlo en
        # REVISAR (remoto atado a país).
        loc = _strip(location.group(1)) if location else ""
        if loc and "remote" not in loc.lower():
            loc = f"Remote - {loc}"
        jobs.append(Job(
            source="linkedin",
            title=_strip(title.group(1)),
            company=_strip(company.group(1)) if company else "",
            url=url,
            location=loc or "Remote",
            salary_text=_strip(salary.group(1)) if salary else "",
            posted_date=posted.group(1) if posted else "",
            easy_apply=easy,
            tags=["easy-apply"] if easy else [],
        ))
    return jobs


def _search(term: str, location: str, *, easy: bool, start: int) -> list[Job]:
    params = {
        "keywords": term,
        "location": location,
        "f_WT": "2",
        "f_TPR": f"r{config.LINKEDIN_HOURS * 3600}",
        "start": start,
    }
    if easy:
        params["f_AL"] = "true"
    r = http_get(SEARCH_URL, params=params, headers=_HEADERS, retries=1)
    return _parse_cards(r.text, easy=easy)


def _job_id(url: str) -> str:
    m = _JOB_ID_RE.search(url)
    return m.group(1) if m else ""


def _desc_priority(job: Job) -> tuple | None:
    """Orden de prioridad para gastar un request de detalle, o None si no
    merece la pena. Gestión ni se pide (se descartaría igual); ingeniería o
    stack en el título sí; Easy Apply va primero dentro de cada grupo."""
    title = " " + _PUNCT_RE.sub(" ", job.title.lower()) + " "
    if any(f" {sig.strip()} " in title for sig in config.MANAGEMENT_SIGNALS):
        return None
    stack = any(kw in title for kw in config.TECH_KEYWORDS)
    engineering = any(sig in title for sig in config.ROLE_SIGNALS)
    if not stack and not engineering:
        return None
    return (not job.easy_apply, not stack)


def _enrich_from_detail(job: Job, detail_html: str) -> None:
    m = _DESC_RE.search(detail_html)
    if m:
        job.description = _strip(m.group(1))
    if not job.salary_text:
        m = _DETAIL_SALARY_RE.search(detail_html)
        if m:
            job.salary_text = _strip(m.group(1))
    for header, value in _CRITERIA_RE.findall(detail_html):
        header, value = _strip(header).lower(), _strip(value)
        if header == "employment type" and not job.employment_type:
            job.employment_type = value
        elif header == "seniority level":
            # Solo como tag informativo: "Mid-Senior level" es el cajón de
            # sastre de LinkedIn y contiene "senior" — inyectarlo en la
            # descripción confundiría a detect_level.
            tag = f"li-seniority: {value.lower()}"
            if tag not in job.tags:
                job.tags.append(tag)


def _enrich_descriptions(jobs: list[Job]) -> None:
    """Baja el detalle de las ofertas prometedoras (1 request por oferta)."""
    cands = [(p, j) for j in jobs
             if _job_id(j.url) and (p := _desc_priority(j)) is not None]
    cands.sort(key=lambda pj: pj[0])
    cands = [j for _, j in cands[:config.LINKEDIN_DESC_MAX]]
    errors = 0
    enriched = 0
    for job in cands:
        # LinkedIn devuelve a veces un 200 sin contenido (authwall
        # intermitente): un segundo intento suele traer el detalle real.
        for _attempt in range(2):
            try:
                r = http_get(DETAIL_URL.format(job_id=_job_id(job.url)),
                             headers=_HEADERS, retries=0)
                _enrich_from_detail(job, r.text)
                errors = 0
            except Exception as exc:  # noqa: BLE001
                errors += 1
                log(f"  linkedin: detail for {job.title[:40]!r} failed: {exc}")
                break
            time.sleep(config.LINKEDIN_DELAY)
            if job.description:
                enriched += 1
                break
        if errors >= 3:
            log("  linkedin: 3 details failed in a row; "
                "stopping enrichment (rate limit?)")
            break
    log(f"  linkedin: {enriched}/{len(cands)} details with description")


def fetch() -> list[Job]:
    by_url: dict[str, Job] = {}
    errors = 0
    aborted = False
    # Easy Apply primero: así el flag queda puesto aunque la pasada general
    # encuentre la misma oferta.
    for easy in (True, False):
        if aborted:
            break
        for location in config.LINKEDIN_LOCATIONS:
            if aborted:
                break
            for term in config.LINKEDIN_SEARCH_TERMS:
                if aborted:
                    break
                start = 0
                for _page in range(config.LINKEDIN_PAGES):
                    try:
                        cards = _search(term, location, easy=easy, start=start)
                        errors = 0
                    except Exception as exc:  # noqa: BLE001
                        errors += 1
                        log(f"  linkedin q={term!r} loc={location!r} "
                            f"easy={easy} start={start}: {exc}")
                        if errors >= 3:
                            log("  linkedin: 3 errors in a row; stopping the "
                                "source (keeping what was collected)")
                            aborted = True
                        break
                    if not cards:
                        break         # no hay más páginas para esta query
                    # La página real es de ~10 tarjetas: avanzar por lo
                    # recibido, no por 25 (saltaría ofertas).
                    start += len(cards)
                    for job in cards:
                        if job.url in by_url:
                            if job.easy_apply:
                                by_url[job.url].easy_apply = True
                        else:
                            by_url[job.url] = job
                    time.sleep(config.LINKEDIN_DELAY)

    jobs = list(by_url.values())
    n_easy = sum(1 for j in jobs if j.easy_apply)
    log(f"linkedin: {len(jobs)} jobs ({n_easy} Easy Apply)")
    if jobs and not aborted:
        _enrich_descriptions(jobs)
    return jobs
