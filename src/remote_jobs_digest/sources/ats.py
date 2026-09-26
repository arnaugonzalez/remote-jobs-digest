"""Fuente ATS — consulta directa a los boards de cada empresa.

Es la fuente de mayor calidad del pipeline y la única sin intermediario: en vez
de esperar a que una oferta aparezca agregada en RemoteOK, se le pregunta a la
empresa. Todos estos endpoints son JSON público sin auth ni scraping:

    greenhouse       boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    lever            api.lever.co/v0/postings/{slug}?mode=json
    ashby            api.ashbyhq.com/posting-api/job-board/{slug}
    workable         apply.workable.com/api/v1/widget/accounts/{slug}?details=true
    recruitee        {slug}.recruitee.com/api/offers/
    smartrecruiters  api.smartrecruiters.com/v1/companies/{slug}/postings
    breezy           {slug}.breezy.hr/json
    personio         {slug}.jobs.personio.de/xml   (XML, no JSON)

La lista de empresas sale de research/data/companies.json (construido por
research/build_companies.py) más CURATED, que añade a mano el ecosistema AI del
handoff §3.4 — esas empresas no salen en los repos de remote-friendly pero son
justo donde el perfil backend+LLM destaca.
"""

from __future__ import annotations

from remote_jobs_digest import paths

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from defusedxml import ElementTree as ET

from remote_jobs_digest import config
from .base import Job, http_get, log

COMPANIES_JSON = str(paths.boards_dir() / "companies.json")
# Boards encontrados probando slugs candidatos contra las APIs
# (research/discover_ats.py). Multiplica por 10 el número de empresas
# consultables respecto a las que declaran su ATS en `careers_url`.
DISCOVERED_JSON = str(paths.boards_dir() / "ats_discovered.json")
# Boards que llevan varios fallos seguidos: se dejan de consultar un tiempo en
# vez de reintentarlos cada día (discover_ats.py solo prueba slugs nuevos, no
# limpia los que ya estaban en el dataset y murieron después).
DEAD_JSON = str(paths.boards_dir() / "ats_dead.json")
DEAD_FAIL_THRESHOLD = 3
DEAD_RETRY_DAYS = 5

# Empresas del ecosistema AI/dev-tools (handoff §3.4). Casi todas remote-first
# y contratan en EU; los slugs están verificados contra el endpoint.
CURATED: list[tuple[str, str, str]] = [
    # (nombre, ats_type, slug)
    ("Anthropic", "greenhouse", "anthropic"),
    ("Databricks", "greenhouse", "databricks"),
    ("Scale AI", "greenhouse", "scaleai"),
    ("Stripe", "greenhouse", "stripe"),
    ("ElevenLabs", "ashby", "elevenlabs"),
    ("Modal", "ashby", "modal"),
    ("LangChain", "ashby", "langchain"),
    ("Hugging Face", "workable", "huggingface"),
    ("Mistral AI", "lever", "mistral"),
    # Patrón "empresa US contrata fuera" (2026-08-21): verificadas en vivo
    # contra el endpoint antes de añadirlas, no adivinadas por nombre.
    ("Cribl", "greenhouse", "cribl"),
    # Ampliación 2026-08-22: 3 agentes en paralelo (GitHub datasets +
    # verificación en vivo por curl) para el mismo patrón, perfil
    # observability/infra/AI. Cada una devolvió jobs reales al verificarla.
    ("Datadog", "greenhouse", "datadog"),
    ("Elastic", "greenhouse", "elastic"),
    ("Brex", "greenhouse", "brex"),
    ("Ramp", "ashby", "ramp"),
    ("Grafana Labs", "greenhouse", "grafanalabs"),
    ("Cohere", "ashby", "cohere"),
    ("Perplexity", "ashby", "perplexity"),
    ("Plaid", "ashby", "plaid"),
    ("Baseten", "ashby", "baseten"),
    ("New Relic", "greenhouse", "newrelic"),
    ("Sentry", "ashby", "sentry"),
    ("PagerDuty", "greenhouse", "pagerduty"),
    ("Honeycomb", "greenhouse", "honeycomb"),
    ("Airbyte", "ashby", "airbyte"),
    ("Rasa", "ashby", "rasa"),
    ("Okta", "greenhouse", "okta"),
    ("Wikimedia", "greenhouse", "wikimedia"),
    ("Deepgram", "ashby", "deepgram"),
    ("Percona", "ashby", "percona"),
    ("Zapier", "ashby", "zapier"),
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")
_MAX_DESC = 4000        # las descripciones de ATS son largas; recortamos


def _clean(html: str | None) -> str:
    """Los ATS devuelven HTML; el clasificador solo necesita el texto."""
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"'))
    return _WS_RE.sub(" ", text).strip()[:_MAX_DESC]


# --- Parsers por ATS --------------------------------------------------------
# Cada uno recibe la respuesta cruda y devuelve Jobs ya normalizados.

def _greenhouse(company: str, slug: str) -> list[Job]:
    r = http_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                 params={"content": "true"})
    out = []
    for j in r.json().get("jobs", []) or []:
        offices = j.get("offices") or []
        loc = (j.get("location") or {}).get("name") or ""
        if not loc and offices:
            loc = ", ".join(o.get("name", "") for o in offices if o.get("name"))
        out.append(Job(
            source=f"ats:greenhouse:{slug}", title=(j.get("title") or "").strip(),
            company=company, url=j.get("absolute_url", ""), location=loc or "",
            description=_clean(j.get("content")),
            posted_date=(j.get("updated_at") or j.get("first_published") or "")))
    return out


def _lever(company: str, slug: str) -> list[Job]:
    r = http_get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    out = []
    for j in r.json() or []:
        cats = j.get("categories") or {}
        out.append(Job(
            source=f"ats:lever:{slug}", title=(j.get("text") or "").strip(),
            company=company, url=j.get("hostedUrl", ""),
            location=cats.get("location", "") or "",
            employment_type=cats.get("commitment", "") or "",
            description=_clean(j.get("descriptionPlain") or j.get("description")),
            tags=[t for t in (cats.get("team"), cats.get("department")) if t]))
    return out


def _ashby(company: str, slug: str) -> list[Job]:
    r = http_get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                 params={"includeCompensation": "true"})
    out = []
    for j in r.json().get("jobs", []) or []:
        comp = j.get("compensation") or {}
        summary = comp.get("compensationTierSummary") or ""
        out.append(Job(
            source=f"ats:ashby:{slug}", title=(j.get("title") or "").strip(),
            company=company, url=j.get("jobUrl", "") or j.get("applyUrl", ""),
            location=j.get("location", "") or "",
            employment_type=j.get("employmentType", "") or "",
            description=_clean(j.get("descriptionPlain") or j.get("descriptionHtml")),
            salary_text=summary,
            posted_date=j.get("publishedAt", "") or "",
            tags=[t for t in [j.get("department"), j.get("team")] if t]))
    return out


def _workable(company: str, slug: str) -> list[Job]:
    r = http_get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
                 params={"details": "true"})
    out = []
    for j in r.json().get("jobs", []) or []:
        loc = j.get("location") or {}
        loc_s = ", ".join(x for x in [loc.get("city"), loc.get("country")] if x)
        if j.get("telecommuting") or loc.get("workplace") == "remote":
            loc_s = f"Remote ({loc_s})" if loc_s else "Remote"
        out.append(Job(
            source=f"ats:workable:{slug}", title=(j.get("title") or "").strip(),
            company=company, url=j.get("url", "") or j.get("shortlink", ""),
            location=loc_s, employment_type=j.get("type", "") or "",
            description=_clean(j.get("description")),
            posted_date=j.get("published_on", "") or ""))
    return out


def _recruitee(company: str, slug: str) -> list[Job]:
    r = http_get(f"https://{slug}.recruitee.com/api/offers/")
    out = []
    for j in r.json().get("offers", []) or []:
        out.append(Job(
            source=f"ats:recruitee:{slug}", title=(j.get("title") or "").strip(),
            company=company, url=j.get("careers_url", "") or j.get("url", ""),
            location=j.get("location", "") or j.get("city", "") or "",
            employment_type=j.get("employment_type", "") or "",
            description=_clean(j.get("description")),
            posted_date=j.get("published_at", "") or "",
            tags=[t for t in [j.get("department")] if t]))
    return out


def _sr_worth_detail(title: str) -> bool:
    """Mismo filtro barato que ya usa LinkedIn (sources/linkedin.py,
    _desc_priority): el segundo request solo se gasta en títulos de
    ingeniería/stack, nunca en gestión."""
    t = " " + _PUNCT_RE.sub(" ", title.lower()) + " "
    if any(f" {sig.strip()} " in t for sig in config.MANAGEMENT_SIGNALS):
        return False
    return (any(kw in t for kw in config.TECH_KEYWORDS)
            or any(sig in t for sig in config.ROLE_SIGNALS))


def _sr_fetch_detail(slug: str, posting_id) -> str:
    r = http_get(f"https://api.smartrecruiters.com/v1/companies/{slug}"
                 f"/postings/{posting_id}")
    sections = (r.json().get("jobAd") or {}).get("sections") or {}
    raw = " ".join((sections.get(key) or {}).get("text") or "" for key in
                   ("jobDescription", "qualifications", "additionalInformation"))
    return _clean(raw)


def _smartrecruiters(company: str, slug: str) -> list[Job]:
    r = http_get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                 params={"limit": 100})
    postings = r.json().get("content", []) or []
    out = []
    for j in postings:
        loc = j.get("location") or {}
        loc_s = ", ".join(x for x in [loc.get("city"), loc.get("country")] if x)
        if loc.get("remote"):
            loc_s = f"Remote ({loc_s})" if loc_s else "Remote"
        out.append(Job(
            source=f"ats:smartrecruiters:{slug}", title=(j.get("name") or "").strip(),
            company=company,
            url=(j.get("ref") or "").replace("api.smartrecruiters.com/v1/companies",
                                             "jobs.smartrecruiters.com"),
            location=loc_s, description="",
            posted_date=j.get("releasedDate", "") or ""))

    # El listado no trae descripción (`jobAd` exige un 2º fetch por oferta);
    # se pide solo para las prometedoras, igual que hace LinkedIn, o
    # ninguna oferta de SmartRecruiters podría pasar el corte de
    # años/stack, que lee texto libre.
    cap = int(os.getenv("RJS_SMARTRECRUITERS_DESC_MAX", "40"))
    cands = [(job, posting) for job, posting in zip(out, postings)
              if _sr_worth_detail(job.title)][:cap]
    if cands:
        with ThreadPoolExecutor(max_workers=5) as pool:
            futs = {pool.submit(_sr_fetch_detail, slug, posting.get("id")): job
                    for job, posting in cands}
            for fut in as_completed(futs):
                job = futs[fut]
                try:
                    job.description = fut.result()
                except Exception:  # noqa: BLE001 — 1 detalle roto no tumba el board
                    pass
    return out


def _breezy(company: str, slug: str) -> list[Job]:
    r = http_get(f"https://{slug}.breezy.hr/json")
    out = []
    for j in r.json() or []:
        loc = j.get("location") or {}
        loc_s = ", ".join(
            x for x in [(loc.get("city") or ""),
                        ((loc.get("country") or {}).get("name") or "")] if x)
        if loc.get("is_remote"):
            loc_s = f"Remote ({loc_s})" if loc_s else "Remote"
        out.append(Job(
            source=f"ats:breezy:{slug}", title=(j.get("name") or "").strip(),
            company=company, url=j.get("url", ""), location=loc_s,
            employment_type=(j.get("type") or {}).get("name", "") or "",
            description=_clean(j.get("description")),
            posted_date=j.get("published_date", "") or ""))
    return out


def _personio(company: str, slug: str) -> list[Job]:
    """Personio publica XML, no JSON."""
    r = http_get(f"https://{slug}.jobs.personio.de/xml")
    out = []
    for pos in ET.fromstring(r.content).iter("position"):
        def g(tag: str, pos=pos) -> str:
            el = pos.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        desc = " ".join(_clean(e.text) for e in pos.iter("value") if e.text)
        pid = g("id")
        out.append(Job(
            source=f"ats:personio:{slug}", title=g("name"), company=company,
            url=f"https://{slug}.jobs.personio.de/job/{pid}" if pid else "",
            location=", ".join(x for x in [g("office"), g("department")] if x),
            employment_type=g("employmentType"), description=desc[:_MAX_DESC],
            posted_date=g("createdAt")))
    return out


PARSERS = {
    "greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby,
    "workable": _workable, "recruitee": _recruitee,
    "smartrecruiters": _smartrecruiters, "breezy": _breezy, "personio": _personio,
}


# --- Orquestación -----------------------------------------------------------

def _load_dead() -> dict:
    if os.path.exists(DEAD_JSON):
        try:
            with open(DEAD_JSON, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001 — fichero corrupto: se regenera
            return {}
    return {}


def _save_dead(dead: dict) -> None:
    os.makedirs(os.path.dirname(DEAD_JSON), exist_ok=True)
    with open(DEAD_JSON, "w", encoding="utf-8") as f:
        json.dump(dead, f, indent=2)


def _targets() -> list[tuple[str, str, str]]:
    """(nombre, ats_type, slug) de CURATED + boards descubiertos + dataset,
    con los boards muertos (>=DEAD_FAIL_THRESHOLD fallos, últimos
    DEAD_RETRY_DAYS días) saltados. RJS_ATS_RETRY_DEAD=1 los vuelve a probar
    todos (por ejemplo tras que `discover_ats.py` refresque el dataset)."""
    targets: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name, kind, slug in CURATED:
        seen.add((kind, slug))
        targets.append((name, kind, slug))
    if os.path.exists(DISCOVERED_JSON):
        with open(DISCOVERED_JSON, encoding="utf-8") as f:
            for d in json.load(f):
                key = (d["ats_type"], d["ats_slug"])
                if key in seen:
                    continue
                seen.add(key)
                targets.append((d["name"], d["ats_type"], d["ats_slug"]))
    if os.path.exists(COMPANIES_JSON):
        with open(COMPANIES_JSON, encoding="utf-8") as f:
            for c in json.load(f):
                kind, slug = c.get("ats_type", ""), c.get("ats_slug", "")
                if not kind or not slug or kind not in PARSERS:
                    continue
                if (kind, slug) in seen:
                    continue
                seen.add((kind, slug))
                targets.append((c["name"], kind, slug))
    else:
        log(f"  ats: no board dataset at {paths.display(COMPANIES_JSON)} "
            f"(run `rjs boards build`); using the 30 curated boards only")

    if os.getenv("RJS_ATS_RETRY_DEAD") == "1":
        return targets
    dead = _load_dead()
    if not dead:
        return targets
    now = datetime.now(timezone.utc)
    alive, skipped = [], 0
    for name, kind, slug in targets:
        rec = dead.get(f"{kind}:{slug}")
        if rec and rec.get("fails", 0) >= DEAD_FAIL_THRESHOLD:
            try:
                age_days = (now - datetime.fromisoformat(rec["last_seen"])).days
            except (KeyError, ValueError):
                age_days = DEAD_RETRY_DAYS  # fecha inválida: mejor reintentar
            if age_days < DEAD_RETRY_DAYS:
                skipped += 1
                continue
        alive.append((name, kind, slug))
    if skipped:
        log(f"  ats: {skipped} boards omitidos por muertos "
            f"(RJS_ATS_RETRY_DEAD=1 para forzar)")
    return alive


def fetch() -> list[Job]:
    targets = _targets()
    log(f"ats: querying {len(targets)} boards...")
    jobs: list[Job] = []
    ok = dead_count = 0
    dead = _load_dead()
    dead_changed = False
    workers = int(os.getenv("RJS_ATS_WORKERS", "14"))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(PARSERS[kind], name, slug): (name, kind, slug)
            for name, kind, slug in targets
        }
        for fut in as_completed(futures):
            name, kind, slug = futures[fut]
            key = f"{kind}:{slug}"
            try:
                got = fut.result()
            except Exception as exc:  # noqa: BLE001 — un board muerto no mata el run
                dead_count += 1
                log(f"  ✗ {kind}:{slug} ({name}): {type(exc).__name__}")
                rec = dead.get(key, {"fails": 0})
                rec["fails"] = rec.get("fails", 0) + 1
                rec["last_seen"] = datetime.now(timezone.utc).isoformat()
                dead[key] = rec
                dead_changed = True
                continue
            ok += 1
            if key in dead:
                del dead[key]  # volvió a responder: se olvida el historial
                dead_changed = True
            # Marcamos la empresa en tags para poder cruzar con el dataset luego.
            for j in got:
                j.tags = list(j.tags) + [f"ats:{kind}"]
            jobs.extend(got)
    if dead_changed:
        _save_dead(dead)
    log(f"ats: {len(jobs)} jobs from {ok} live boards ({dead_count} dead)")
    return jobs
