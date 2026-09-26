"""Daily run: collect from every active source, filter, score, digest.

    rjs run                        # active sources from config.yaml
    rjs run --no-ai                # deterministic filters only (zero tokens)
    rjs run --sources ats,remotive
    rjs run --top 20 --no-telegram
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from remote_jobs_digest import config, paths
from remote_jobs_digest import filters
from remote_jobs_digest import exporters
from remote_jobs_digest import notify
from remote_jobs_digest import ai_filter
from remote_jobs_digest.sources import REGISTRY
from remote_jobs_digest.sources.base import Job, log

_WS_RE = re.compile(r"\s+")


def _dedup_key(job: Job) -> str:
    title = _WS_RE.sub(" ", job.title.lower()).strip()
    company = _WS_RE.sub(" ", job.company.lower()).strip()
    return f"{title}@{company}"


def collect(source_names: list[str]) -> list[Job]:
    jobs: list[Job] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for name in source_names:
        fetch = REGISTRY.get(name)
        if not fetch:
            log(f"unknown source: {name} (ignored)")
            continue
        try:
            fetched = fetch()
        except Exception as exc:  # noqa: BLE001 — una fuente caída no mata el run
            log(f"source '{name}' failed entirely: {type(exc).__name__}: {exc}")
            continue
        for job in fetched:
            if not job.title or not job.url:
                continue
            if job.id in seen_ids:
                continue
            key = _dedup_key(job)
            if key in seen_keys:      # mismo puesto desde otra fuente
                continue
            seen_ids.add(job.id)
            seen_keys.add(key)
            jobs.append(job)
    return jobs


SEEN_PATH = os.path.join(config.OUTPUT_DIR, "seen_ids.json")


def _mark_new(jobs: list[Job]) -> int:
    """Marca las ofertas no vistas en runs anteriores y actualiza seen_ids.

    Sin esto, el digest diario repetía las mismas 12 ofertas cada mañana y lo
    nuevo quedaba enterrado. La primera ejecución marca todo como nuevo (no hay
    fichero previo), lo cual es correcto.
    """
    seen: set[str] = set()
    if os.path.exists(SEEN_PATH):
        try:
            with open(SEEN_PATH, encoding="utf-8") as f:
                seen = set(json.load(f))
        except Exception:  # noqa: BLE001 — fichero corrupto: se regenera
            seen = set()
    n_new = 0
    for j in jobs:
        j.is_new = j.id not in seen
        n_new += j.is_new
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(seen | {j.id for j in jobs}), f)
    return n_new


def run(source_names: list[str], send_telegram: bool, top_n: int,
        use_ai: bool, quiet: bool = False) -> dict:
    log(f"Sources: {', '.join(source_names)}")
    all_jobs = collect(source_names)
    n_new = _mark_new(all_jobs)
    log(f"Unique after dedup: {len(all_jobs)} ({n_new} new)")

    for job in all_jobs:
        filters.process(job)

    # Optional LLM pass, only over what survived the cheap filters.
    ai_used = False
    if use_ai:
        candidates = exporters.pre_ai_candidates(all_jobs)
        ai_used = ai_filter.score_jobs(candidates)

    config.DIGEST_TOP_N = top_n
    filtered = exporters.filter_and_sort(all_jobs, ai_used=ai_used)
    stats = exporters.save_results(all_jobs, filtered)

    digest = notify.build_digest(filtered, stats)
    stats["digest_path"] = str(notify.write_digest(digest))

    print()
    print(f"  ✅ match:     {stats['apta']}")
    print(f"  ⚠️  review:    {stats['revisar']}")
    print(f"  ❌ rejected:  {stats['descartada']}")
    print(f"  🚫 management/staff+ dropped (stack matched): {stats['mgmt_dropped']}")
    print(f"  🏢 consultancy dropped (stack matched):       {stats['consulting_dropped']}")
    print(f"  ⚠️  with exclusivity clause: {stats['exclusivity']}")
    print(f"  📋 final list (IC, stack >= {config.MIN_STACK_SCORE}): {stats['filtered']}")
    print(f"  🤖 LLM: {'on (' + config.AI_MODEL + ')' if ai_used else 'off'}")
    saved = [Path(stats[k]) for k in ("csv_path", "raw_path", "digest_path")]
    print(f"\n  💾 {paths.display(saved[0].parent)}/  {'  '.join(p.name for p in saved)}")
    if not quiet:
        print("\n" + notify.for_terminal(digest))

    if send_telegram and notify.telegram_configured():
        notify.send_telegram(digest)
    return stats


def build_parser(p: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    p = p or argparse.ArgumentParser(prog="rjs run", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sources", default=None,
                   help="comma-separated source names (default: search.active_sources)")
    p.add_argument("--no-telegram", action="store_true",
                   help="do not send the digest to Telegram even if configured")
    p.add_argument("--no-ai", action="store_true",
                   help="skip the optional LLM pass (zero tokens)")
    p.add_argument("--top", type=int, default=None,
                   help="number of jobs in the digest (default 12)")
    p.add_argument("--quiet", action="store_true",
                   help="do not print the digest to stdout")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    names = args.sources or ",".join(config.ACTIVE_SOURCES)
    sources = [s.strip() for s in names.split(",") if s.strip()]
    run(sources, send_telegram=not args.no_telegram,
        top_n=args.top or config.DIGEST_TOP_N,
        use_ai=not args.no_ai and config.USE_AI_FILTER, quiet=args.quiet)


if __name__ == "__main__":
    main()
