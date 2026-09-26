#!/usr/bin/env python3
"""`rjs boards build`: the seed list of remote-friendly companies.

Merges public GitHub lists (structured data, no scraping):

  remoteintech/remote-jobs   -> company pages with YAML frontmatter (region,
                                remote_policy, technologies, careers_url)
  remote-es/remotes          -> companies hiring in Spain on local contracts
  yanirs/established-remote  -> established remote companies, and whether they
                                pay globally competitive rates
  lukasz-madon/awesome-remote-job

Writes companies.json / companies.csv into the boards dir (`rjs paths`), with
a stack score and the ATS detected from careers_url. `rjs boards discover`
then probes ATS slugs for the companies without one.

    rjs boards build --fetch    # download the lists, then build
    rjs boards build            # rebuild from the cached downloads
"""

from __future__ import annotations

from remote_jobs_digest import paths

import argparse
import csv
import json
import os
import re
import sys
import tarfile
from dataclasses import dataclass, field, asdict

import httpx

DATA = str(paths.boards_dir())
RAW = os.path.join(DATA, "raw")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

SOURCES = {
    "remote-es": "https://raw.githubusercontent.com/remote-es/remotes/master/README.md",
    "established": "https://raw.githubusercontent.com/yanirs/established-remote/master/README.md",
    "awesome-remote": "https://raw.githubusercontent.com/lukasz-madon/awesome-remote-job/master/README.md",
}
RIT_TARBALL = "https://api.github.com/repos/remoteintech/remote-jobs/tarball/main"


# --- Puntuación de stack (handoff §4) --------------------------------------
# +3 backend Python · +3 AI/LLM en producción · +2 infra/cloud · +1 mobile
# +1 entorno regulado (fintech/insurtech). El máximo teórico es 10.
STACK_WEIGHTS = [
    (3, "backend_python", ["python", "fastapi", "django", "flask", "celery",
                           "sqlalchemy", "pydantic"]),
    (3, "ai_llm", ["llm", "genai", "generative ai", "langchain", "langgraph",
                   "rag", "machine learning", "ml", "mlops", "nlp", "ai",
                   "deep learning", "data science", "pytorch", "tensorflow"]),
    (2, "infra", ["aws", "terraform", "kubernetes", "k8s", "docker", "devops",
                  "gcp", "azure", "cloud", "sre", "infrastructure"]),
    (1, "mobile", ["react native", "flutter", "mobile", "ios", "android",
                   "kotlin", "swift", "dart"]),
    (1, "regulated", ["fintech", "insurtech", "insurance", "banking",
                      "payments", "finance", "regtech", "compliance"]),
]

# Consultoría/outsourcing: exactamente el modelo que se quiere dejar (§4).
CONSULTING_HINTS = ["consulting", "consultancy", "outsourcing", "nearshore",
                    "offshore", "staffing", "agency", "body shop",
                    "it services", "software development services"]


@dataclass
class Company:
    name: str
    website: str = ""
    careers_url: str = ""
    region: str = ""            # worldwide / europe / americas / ...
    remote_policy: str = ""     # fully-remote / remote-first / hybrid / ...
    company_size: str = ""
    technologies: list[str] = field(default_factory=list)
    business: str = ""
    hires_spain: bool = False       # contrato español (remote-es)
    global_comp: str = ""           # ✓ / ✗ / ? (established-remote)
    sources: list[str] = field(default_factory=list)
    ats_type: str = ""              # greenhouse / lever / ashby / ...
    ats_slug: str = ""
    stack_score: int = 0
    stack_hits: list[str] = field(default_factory=list)
    is_consulting: bool = False
    verdict: str = ""
    reason: str = ""
    notes: str = ""


# --- Descarga ---------------------------------------------------------------

def _curl(url: str, dest: str) -> bool:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=90,
                      follow_redirects=True)
    except httpx.HTTPError as exc:
        print(f"  FAIL {type(exc).__name__} {url}", file=sys.stderr)
        return False
    ok = r.status_code == 200
    if ok:
        with open(dest, "wb") as f:
            f.write(r.content)
    print(f"  {'ok ' if ok else 'FAIL'} {r.status_code} {url}", file=sys.stderr)
    return ok


def fetch_all() -> None:
    print("Descargando fuentes...", file=sys.stderr)
    for name, url in SOURCES.items():
        _curl(url, os.path.join(RAW, f"{name}.md"))
    tarball = os.path.join(RAW, "remoteintech.tar.gz")
    if _curl(RIT_TARBALL, tarball):
        dest = os.path.join(RAW, "rit")
        os.makedirs(dest, exist_ok=True)
        with tarfile.open(tarball) as tf:
            members = [m for m in tf.getmembers()
                       if "/src/companies/" in m.name and m.name.endswith(".md")]
            for m in members:
                m.name = os.path.basename(m.name)
                tf.extract(m, dest)
        print(f"  {len(members)} fichas remoteintech", file=sys.stderr)


# --- Parsers ----------------------------------------------------------------

_FM_RE = re.compile(r"^---\s*$")


def parse_remoteintech(path: str) -> list[Company]:
    """Cada ficha es un markdown con frontmatter YAML plano (sin nesting)."""
    out: list[Company] = []
    if not os.path.isdir(path):
        return out
    for fn in sorted(os.listdir(path)):
        if not fn.endswith(".md"):
            continue
        with open(os.path.join(path, fn), encoding="utf-8") as f:
            lines = f.read().splitlines()
        if not lines or not _FM_RE.match(lines[0]):
            continue
        end = next((i for i, l in enumerate(lines[1:], 1) if _FM_RE.match(l)), None)
        if end is None:
            continue
        fm: dict[str, str] = {}
        techs: list[str] = []
        in_techs = False
        for line in lines[1:end]:
            if line.startswith("  - ") and in_techs:
                techs.append(line[4:].strip())
                continue
            in_techs = False
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "technologies":
                in_techs = True
                continue
            fm[k] = v
        name = fm.get("title") or fm.get("slug") or fn[:-3]
        if not name:
            continue
        out.append(Company(
            name=name,
            website=fm.get("website", ""),
            careers_url=fm.get("careers_url", ""),
            region=fm.get("region", ""),
            remote_policy=fm.get("remote_policy", ""),
            company_size=fm.get("company_size", ""),
            technologies=techs,
            sources=["remoteintech"],
        ))
    return out


_ES_ITEM_RE = re.compile(r"^\*\s+(.+?)\s*\[([^\]]+)\]\(([^)]+)\)\s*(.*)$")


def parse_remote_es(path: str) -> list[Company]:
    """Sección '# Companies': `* Nombre [Open positions](url) (anotaciones)`."""
    out: list[Company] = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    try:
        start = next(i for i, l in enumerate(lines)
                     if l.strip().lower().startswith("# companies"))
    except StopIteration:
        return out
    for line in lines[start + 1:]:
        if line.startswith("# "):          # siguiente sección
            break
        m = _ES_ITEM_RE.match(line.strip())
        if not m:
            continue
        name, _label, url, notes = m.groups()
        out.append(Company(
            name=name.strip(),
            careers_url=url.strip(),
            hires_spain=True,
            region="spain",
            sources=["remote-es"],
            notes=notes.strip(),
        ))
    return out


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def parse_established(path: str) -> list[Company]:
    """Tabla markdown sin pipes de borde: Company | Business | Tech | Comp | Links."""
    out: list[Company] = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for line in lines:
        if line.count("|") < 4 or line.lstrip().startswith("---"):
            continue
        cols = [c.strip() for c in line.split("|")]
        m = _MD_LINK_RE.search(cols[0])
        if not m:
            continue
        name, website = m.group(1), m.group(2)
        if name.lower() == "company":
            continue
        careers = ""
        for link_text, link_url in _MD_LINK_RE.findall(cols[4] if len(cols) > 4 else ""):
            if "job" in link_text.lower() or "career" in link_text.lower():
                careers = link_url
                break
        techs = [t.strip().lower() for t in cols[2].split(",") if t.strip()]
        out.append(Company(
            name=name,
            website=website,
            careers_url=careers,
            business=cols[1],
            technologies=techs,
            global_comp=cols[3],
            sources=["established-remote"],
        ))
    return out


# --- Fusión y enriquecimiento -----------------------------------------------

def _norm(name: str) -> str:
    n = name.lower().strip()
    n = re.sub(r"\b(inc|llc|ltd|gmbh|s\.?l\.?|s\.?a\.?|corp|co)\b\.?", "", n)
    return re.sub(r"[^a-z0-9]", "", n)


ATS_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards|boards-api)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-zA-Z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_.-]+)")),
    # apply.workable.com/{slug}/ y {slug}.workable.com son ambos válidos: el
    # host genérico lleva el slug en el path, así que se prueba primero.
    ("workable", re.compile(r"apply\.workable\.com/([a-zA-Z0-9_-]+)")),
    ("workable", re.compile(r"(?!apply)([a-zA-Z0-9_-]+)\.workable\.com")),
    ("recruitee", re.compile(r"([a-zA-Z0-9_-]+)\.recruitee\.com")),
    ("breezy", re.compile(r"([a-zA-Z0-9_-]+)\.breezy\.hr")),
    ("smartrecruiters", re.compile(r"smartrecruiters\.com/([a-zA-Z0-9_-]+)")),
    # Teamtailor admite un shard regional intercalado (foo.na.teamtailor.com):
    # el slug es siempre la primera etiqueta del host.
    ("teamtailor", re.compile(r"//([a-zA-Z0-9_-]+)\.(?:[a-z]{2}\.)?teamtailor\.com")),
    ("personio", re.compile(r"([a-zA-Z0-9_-]+)\.jobs\.personio\.")),
    ("factorial", re.compile(r"([a-zA-Z0-9_-]+)\.factorialhr\.")),
    ("workday", re.compile(r"([a-zA-Z0-9_-]+)\.wd\d+\.myworkdayjobs\.com")),
]


def detect_ats(company: Company) -> None:
    for url in (company.careers_url, company.website):
        if not url:
            continue
        for kind, rx in ATS_PATTERNS:
            m = rx.search(url)
            if m:
                company.ats_type, company.ats_slug = kind, m.group(1)
                return


def score_stack(company: Company) -> None:
    """Puntúa 0-10 el encaje del stack con el perfil del handoff."""
    haystack = " ".join([
        " ".join(company.technologies), company.business, company.name,
    ]).lower()
    score, hits = 0, []
    for weight, label, keywords in STACK_WEIGHTS:
        if any(re.search(rf"\b{re.escape(kw)}\b", haystack) for kw in keywords):
            score += weight
            hits.append(label)
    company.stack_score, company.stack_hits = score, hits
    company.is_consulting = any(h in haystack for h in CONSULTING_HINTS)


# Regiones desde las que un residente en España puede trabajar.
_OK_REGIONS = {"worldwide", "europe", "americas-europe", "spain"}
_OK_POLICIES = {"fully-remote", "remote-first"}


def classify(c: Company) -> None:
    """APTA / REVISAR / DESCARTADA — la geografía es eliminatoria (§4)."""
    if c.remote_policy == "hybrid":
        c.verdict, c.reason = "DESCARTADA", "remote_policy=hybrid (requiere oficina)"
        return
    if c.region in {"americas", "asia-pacific"}:
        c.verdict, c.reason = "DESCARTADA", f"región {c.region}: no contrata en EU"
        return
    if c.is_consulting and not c.hires_spain:
        c.verdict, c.reason = "DESCARTADA", "consultoría/outsourcing (§4: modelo a evitar)"
        return
    if c.stack_score < 3:
        c.verdict, c.reason = "REVISAR", f"stack sin señal fuerte (score {c.stack_score})"
        return

    reasons = []
    if c.hires_spain:
        reasons.append("contrata en España (contrato español)")
    if c.region in _OK_REGIONS:
        reasons.append(f"región {c.region}")
    if c.remote_policy in _OK_POLICIES:
        reasons.append(c.remote_policy)
    if c.global_comp == "✓":
        reasons.append("comp. globalmente competitiva")

    if (c.hires_spain or c.region in _OK_REGIONS) and c.stack_score >= 5:
        c.verdict = "APTA"
    elif c.hires_spain or c.region in _OK_REGIONS:
        c.verdict = "APTA" if c.remote_policy in _OK_POLICIES else "REVISAR"
    else:
        c.verdict = "REVISAR"
    c.reason = f"stack {c.stack_score}/10 ({'+'.join(c.stack_hits)}); " + ", ".join(reasons)


def merge(groups: list[list[Company]]) -> list[Company]:
    """Fusiona por nombre normalizado. Las señales se acumulan, no se pisan."""
    by_key: dict[str, Company] = {}
    for group in groups:
        for c in group:
            key = _norm(c.name)
            if not key:
                continue
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = c
                continue
            # Campos escalares: el primero no vacío gana.
            for f_ in ("website", "careers_url", "region", "remote_policy",
                       "company_size", "business", "global_comp", "notes"):
                if not getattr(existing, f_) and getattr(c, f_):
                    setattr(existing, f_, getattr(c, f_))
            existing.hires_spain = existing.hires_spain or c.hires_spain
            if c.hires_spain:
                existing.region = "spain"
            existing.technologies = sorted(set(existing.technologies) | set(c.technologies))
            existing.sources = sorted(set(existing.sources) | set(c.sources))
    return list(by_key.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="download the source lists first (needed on first run)")
    args = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    if args.fetch:
        fetch_all()

    groups = [
        parse_remote_es(os.path.join(RAW, "remote-es.md")),
        parse_established(os.path.join(RAW, "established.md")),
        parse_remoteintech(os.path.join(RAW, "rit")),
    ]
    for g, label in zip(groups, ("remote-es", "established", "remoteintech")):
        print(f"  {label}: {len(g)} empresas", file=sys.stderr)

    companies = merge(groups)
    for c in companies:
        detect_ats(c)
        score_stack(c)
        classify(c)
    companies.sort(key=lambda c: (-c.stack_score, not c.hires_spain, c.name.lower()))

    json_path = os.path.join(DATA, "companies.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([asdict(c) for c in companies], f, indent=2, ensure_ascii=False)

    csv_path = os.path.join(DATA, "companies.csv")
    cols = ["verdict", "name", "stack_score", "stack_hits", "hires_spain",
            "region", "remote_policy", "company_size", "global_comp",
            "ats_type", "ats_slug", "careers_url", "website", "technologies",
            "business", "sources", "reason", "notes"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for c in companies:
            row = asdict(c)
            for k in ("technologies", "sources", "stack_hits"):
                row[k] = ", ".join(row[k])
            w.writerow(row)

    n_apta = sum(1 for c in companies if c.verdict == "APTA")
    n_rev = sum(1 for c in companies if c.verdict == "REVISAR")
    n_desc = sum(1 for c in companies if c.verdict == "DESCARTADA")
    n_es = sum(1 for c in companies if c.hires_spain)
    n_ats = sum(1 for c in companies if c.ats_type)
    print(f"\n  Unique companies: {len(companies)}")
    print(f"  ✅ APTA {n_apta} · ⚠️ REVISAR {n_rev} · ❌ DESCARTADA {n_desc}")
    print(f"  🇪🇸 hire in Spain (remote-es list): {n_es}")
    print(f"  🔌 with an ATS queryable by API: {n_ats}")
    print(f"\n  💾 {json_path}\n  💾 {csv_path}")


if __name__ == "__main__":
    main()
