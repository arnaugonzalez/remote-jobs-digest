#!/usr/bin/env python3
"""Descubre boards ATS probando slugs candidatos contra las APIs públicas.

El dataset trae 1.137 empresas, pero solo ~86 exponen su ATS en `careers_url`.
Del resto sabemos el nombre, y los slugs de ATS son casi siempre una
normalización de ese nombre ("Hugging Face" -> "huggingface"). Así que se
generan candidatos y se prueban contra los endpoints; los que responden 200 con
ofertas se añaden al dataset.

Es fuerza bruta acotada: 3-4 slugs por empresa y por ATS, todos GET a APIs
públicas y con concurrencia moderada. Un 404 significa "ese board no existe",
que es exactamente la información que buscamos.

Uso:
    rjs boards discover                              # every company without an ATS
    rjs boards discover --limit 200                  # partial run
    rjs boards discover --curated-only               # only the built-in list
    rjs boards discover --names-file names.json      # extra company names (JSON list)
    rjs boards discover --boards-file boards.json    # known {name, ats_type, ats_slug} to verify
"""

from __future__ import annotations

from remote_jobs_digest import paths

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

COMPANIES = str(paths.boards_dir() / "companies.json")
FOUND = str(paths.boards_dir() / "ats_discovered.json")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 12.0

# (nombre, plantilla de URL, clave del JSON con la lista de ofertas)
# Workable, Recruitee, SmartRecruiters y Personio son mucho más habituales en
# empresas EUROPEAS que Greenhouse/Lever (dominantes en EE.UU.), así que son los
# que más elevan el número de ofertas con geografía compatible.
PROBES = [
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/{s}/jobs", "jobs"),
    ("ashby", "https://api.ashbyhq.com/posting-api/job-board/{s}", "jobs"),
    ("lever", "https://api.lever.co/v0/postings/{s}?mode=json", None),
    ("workable",
     "https://apply.workable.com/api/v1/widget/accounts/{s}?details=true",
     "jobs"),
    ("recruitee", "https://{s}.recruitee.com/api/offers/", "offers"),
    ("smartrecruiters",
     "https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=100",
     "content"),
    ("breezy", "https://{s}.breezy.hr/json", None),
]

# Empresas donde el perfil (backend Python + AI/LLM, remoto EU) encaja mejor.
# Se prueban siempre, estén o no en el dataset de remote-friendly.
CURATED_NAMES = [
    # AI labs y plataformas LLM
    "Anthropic", "OpenAI", "Mistral AI", "Cohere", "Hugging Face", "Scale AI",
    "ElevenLabs", "Perplexity", "Runway", "Stability AI", "Together AI",
    "Anyscale", "LangChain", "LlamaIndex", "Weights & Biases", "Comet ML",
    "Humanloop", "Fireworks AI", "Groq", "Replicate", "Modal", "Baseten",
    "Cerebras", "AssemblyAI", "Deepgram", "Speechmatics", "Gladia",
    "Contextual AI", "Sierra AI", "Harvey", "Abridge", "Cresta", "Glean",
    "Writer", "Jasper", "Copy AI", "Typeface", "Synthesia", "HeyGen",
    "Descript", "Suno", "ElevenLabs", "Luma AI", "Pika", "Captions",
    # Infraestructura de datos y vectores
    "Weaviate", "Qdrant", "Pinecone", "Chroma", "Milvus", "Zilliz",
    "Supabase", "Neon", "PlanetScale", "Timescale", "ClickHouse", "Databricks",
    "Snowflake", "dbt Labs", "Airbyte", "Fivetran", "Prefect", "Dagster",
    "Astronomer", "Temporal", "Redpanda", "Confluent", "StarTree", "Materialize",
    # Dev tools y plataformas
    "Vercel", "Netlify", "Render", "Railway", "Fly.io", "Cloudflare",
    "DigitalOcean", "Grafana Labs", "Datadog", "Sentry", "Honeycomb",
    "Sourcegraph", "GitLab", "GitHub", "JetBrains", "Postman", "Hasura",
    "Prisma", "Doppler", "Vanta", "Snyk", "Chainguard", "Docker", "HashiCorp",
    "Pulumi", "Spacelift", "Northflank", "Porter", "Depot",
    # Remote-first con backend Python fuerte
    "Automattic", "Zapier", "Doist", "Buffer", "Ghost", "Hotjar", "Toggl",
    "Remote", "Deel", "Oyster", "Papaya Global", "Multiplier",
    "Stripe", "Wise", "Revolut", "Monzo", "Mollie", "Adyen", "Checkout.com",
    "Affirm", "Klarna", "Sezzle", "Marqeta", "Ramp", "Brex", "Mercury",
    "Plaid", "TrueLayer", "GoCardless", "SumUp", "Payhawk", "Pleo",
    "Typeform", "Factorial", "Jobandtalent", "Glovo", "Cabify", "Wallbox",
    "Seedtag", "Red Points", "Signaturit", "Holded", "Declarando",
    "Docplanner", "Preply", "Babbel", "Busuu", "Duolingo", "Brainly",
    "Bitpanda", "N26", "Trade Republic", "Scalable Capital", "Raisin",
    "Personio", "Miro", "Pitch", "Contentful", "Storyblok", "Strapi",
    "Algolia", "Meilisearch", "Elastic", "Bitmovin", "Mux", "Livekit",
    "Twilio", "Vonage", "MessageBird", "Sinch", "Infobip",
    "Canonical", "Red Hat", "SUSE", "Mozilla", "Wikimedia", "Internet Archive",
    "Sword Health", "Kry", "Alan", "Ada Health", "Huma", "Cera",
    "Bolt", "Wolt", "Delivery Hero", "Just Eat Takeaway", "HelloFresh",
    "Trivago", "GetYourGuide", "Omio", "FlixBus", "Blablacar",
    # --- España: donde el contrato local es posible ---
    "Amenitiz", "Latitude", "Cobee", "Payflow", "Bit2Me", "Clarity AI",
    "Ontruck", "Paack", "Exoticca", "Housfy", "Clicars", "Colvin",
    "Genially", "Playtomic", "Fever", "Badi", "Spotahome", "Cover Manager",
    "Freepik", "Flywire", "Sngular", "Openbank", "Ebury", "Devengo",
    "Embat", "Belvo", "Cuideo", "Mediquo", "Savana", "Idoven", "Legit Health",
    "Nemuru", "Unnax", "Mundimoto", "Wallapop", "Jeff", "Coverfy",
    "Multiverse Computing", "Nommon", "Sherpa.ai", "Bitphy", "Stratio",
    "Kernel Analytics", "Denodo", "Priceless", "Fintonic", "Tinybird",
    "Vottun", "Barkibu", "Lang.ai", "Kompyte", "Restb.ai", "Nteract",
    # --- Europa remote-first con backend Python / AI ---
    "Aiven", "Supermetrics", "Smartly.io", "Sanoma", "Relex", "Wolt",
    "Zalando", "Delivery Hero", "SoundCloud", "Ada Support", "Klaus",
    "Veriff", "Wise", "Bolt", "Pipedrive", "Glia", "Salv", "Modularbank",
    "Docker", "Grafana", "Sinch", "Kahoot", "Cognite", "Xeneta", "Tibber",
    "Einride", "Klarna", "Truecaller", "Voi", "Northvolt", "Epidemic Sound",
    "Lunar", "Pleo", "Corti", "Templafy", "Dixa", "Podimo", "Trustpilot",
    "Unity", "Zendesk", "Siteimprove", "Peakon", "Blue Yonder",
    "Contentsquare", "Dataiku", "Hugging Face", "Owkin", "Shift Technology",
    "Ledger", "Qonto", "Swile", "Payfit", "Spendesk", "Alan", "Doctolib",
    "Back Market", "ManoMano", "Younited", "Lydia", "Sorare", "Ankorstore",
    "Pennylane", "Aircall", "Front", "PhotoRoom", "Mirakl", "Akeneo",
    "Adevinta", "Schibsted", "Bynder", "Mews", "Productboard", "Rossum",
    "Kiwi.com", "Socialbakers", "Apify", "Cognitive Security", "Gymbeam",
    "Bolt.eu", "InPost", "Booksy", "Brainly", "Vinted", "Nord Security",
    "Tesonet", "Kilo Health", "Genius Sports", "Hostinger", "Omnisend",
    "Whereby", "Remote Technology", "Oyster HR", "Hotjar", "Kaizen Gaming",
    "Blueground", "Persado", "Workable", "Skroutz", "Beat", "Viva Wallet",
    "Softonic", "Tiledesk", "Musixmatch", "Satispay", "Scalapay", "Casavo",
    "Bending Spoons", "Prima Assicurazioni", "Yolo Group", "Cloudinary",
]


def slug_variants(name: str) -> list[str]:
    """Slugs plausibles para un nombre de empresa, del más probable al menos."""
    base = name.lower().strip()
    base = re.sub(r"\s*\(.*?\)\s*", " ", base)          # quita paréntesis
    base = re.sub(r"[''`.,]", "", base)
    base = re.sub(r"\b(inc|llc|ltd|gmbh|sl|sa|corp|co|the|group|labs?|"
                  r"technologies|technology|software|company)\b", " ", base)
    base = re.sub(r"&", "and", base)
    words = [w for w in re.split(r"[^a-z0-9]+", base) if w]
    if not words:
        return []
    joined = "".join(words)
    out = [joined]
    if len(words) > 1:
        out.append("-".join(words))
        out.append(words[0])
    # Muchos boards usan el dominio: "fly.io" -> "flyio"
    return [s for s in dict.fromkeys(out) if 2 < len(s) < 40]


def probe(client: httpx.Client, kind: str, url_tpl: str, key: str | None,
          slug: str) -> int:
    """Nº de ofertas del board, o -1 si no existe."""
    try:
        r = client.get(url_tpl.format(s=slug))
        if r.status_code != 200:
            return -1
        data = r.json()
        jobs = data.get(key, []) if key else data
        return len(jobs) if isinstance(jobs, list) else -1
    except Exception:  # noqa: BLE001 — un board inexistente no es un error
        return -1


_PROBE_BY_KIND = {kind: (tpl, key) for kind, tpl, key in PROBES}


def verify_board(entry: dict) -> dict | None:
    """Confirma en vivo un par (ats_type, ats_slug) ya conocido (sin adivinar
    slugs): una única petición, no la combinatoria de slug_variants()."""
    kind, slug = entry.get("ats_type"), entry.get("ats_slug")
    probe_def = _PROBE_BY_KIND.get(kind)
    if not probe_def or not slug:
        return None
    tpl, key = probe_def
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA},
                      follow_redirects=True) as client:
        n = probe(client, kind, tpl, key, slug)
    if n <= 0:
        return None
    return {"name": entry.get("name") or slug, "ats_type": kind,
            "ats_slug": slug, "n_jobs": n}


def discover(name: str) -> dict | None:
    """Primer board vivo y con ofertas para esta empresa."""
    variants = slug_variants(name)
    if not variants:
        return None
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA},
                      follow_redirects=True) as client:
        for kind, tpl, key in PROBES:
            for slug in variants:
                n = probe(client, kind, tpl, key, slug)
                if n > 0:
                    return {"name": name, "ats_type": kind, "ats_slug": slug,
                            "n_jobs": n}
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="max companies to probe")
    ap.add_argument("--curated-only", action="store_true", help="only probe the built-in curated list")
    ap.add_argument("--workers", type=int, default=10, help="parallel requests (default 10)")
    ap.add_argument("--names-file", action="append", default=[],
                     help="JSON list of extra company names to probe "
                          "(repeatable), e.g. a list of YC company names")
    ap.add_argument("--boards-file", action="append", default=[],
                     help="JSON list of known {name, ats_type, ats_slug} to "
                          "verify live (one request each, no slug guessing). "
                          "Repeatable.")
    args = ap.parse_args()

    # Boards ya descubiertos en corridas anteriores: se cargan primero para
    # que este run los preserve en el fichero de salida en vez de pisarlos
    # (antes, un --curated-only o un --names-file parcial hacía que
    # json.dump(found, ...) sobreescribiera FOUND solo con lo hallado en ESTA
    # corrida, perdiendo silenciosamente los boards de corridas previas).
    existing_found: list[dict] = []
    if os.path.exists(FOUND):
        with open(FOUND, encoding="utf-8") as f:
            existing_found = json.load(f)

    names: list[str] = list(CURATED_NAMES)
    for path in args.names_file:
        with open(path, encoding="utf-8") as f:
            names += json.load(f)

    known: set[tuple[str, str]] = {(d["ats_type"], d["ats_slug"]) for d in existing_found}
    if os.path.exists(COMPANIES):
        with open(COMPANIES, encoding="utf-8") as f:
            companies = json.load(f)
        for c in companies:
            if c.get("ats_type") and c.get("ats_slug"):
                known.add((c["ats_type"], c["ats_slug"]))
        if not args.curated_only:
            # Prioriza las que ya puntúan alto en stack o contratan en España.
            rest = [c for c in companies if not c.get("ats_type")]
            rest.sort(key=lambda c: (-c.get("stack_score", 0),
                                     not c.get("hires_spain")))
            names += [c["name"] for c in rest]
    # Dedup conservando orden.
    seen: set[str] = set()
    names = [n for n in names if not (n.lower() in seen or seen.add(n.lower()))]
    if args.limit:
        names = names[:args.limit]

    boards: list[dict] = []
    for path in args.boards_file:
        with open(path, encoding="utf-8") as f:
            boards += json.load(f)
    boards = [b for b in boards if (b.get("ats_type"), b.get("ats_slug")) not in known]
    b_seen: set[tuple[str, str]] = set()
    boards = [b for b in boards
              if not ((b["ats_type"], b["ats_slug"]) in b_seen
                      or b_seen.add((b["ats_type"], b["ats_slug"])))]

    print(f"Probando {len(names)} empresas (slug adivinado) + "
          f"{len(boards)} boards conocidos (verificación directa) contra "
          f"{len(PROBES)} ATS...", file=sys.stderr)

    found: list[dict] = []
    done = 0
    total = len(names) + len(boards)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(discover, n): "discover" for n in names}
        futures.update({pool.submit(verify_board, b): "verify" for b in boards})
        for fut in as_completed(futures):
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{total} · {len(found)} boards",
                      file=sys.stderr)
            try:
                res = fut.result()
            except Exception:  # noqa: BLE001
                continue
            if not res:
                continue
            if (res["ats_type"], res["ats_slug"]) in known:
                continue
            known.add((res["ats_type"], res["ats_slug"]))
            found.append(res)
            print(f"  ✓ {res['ats_type']:11} {res['ats_slug']:26} "
                  f"{res['n_jobs']:>4} ofertas  ({res['name']})",
                  file=sys.stderr)

    merged = existing_found + found
    merged.sort(key=lambda r: -r["n_jobs"])
    os.makedirs(os.path.dirname(FOUND), exist_ok=True)
    with open(FOUND, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    total_new = sum(r["n_jobs"] for r in found)
    total = sum(r["n_jobs"] for r in merged)
    print(f"\n  {len(found)} boards nuevos ({total_new:,} ofertas) · "
          f"{len(merged)} boards totales ({total:,} ofertas)")
    print(f"  💾 {FOUND}")


if __name__ == "__main__":
    main()
