"""The daily digest: always a Markdown file, optionally a Telegram message.

Every job line credits its source ("via Remote OK"): Remote OK, Remotive and
Himalayas grant API access on condition that you link back and name them.
Telegram messages stay under its 4096-char limit by capping at DIGEST_TOP_N.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from remote_jobs_digest import config
from remote_jobs_digest.sources.base import Job, log

SOURCE_NAMES = {
    "remoteok": "Remote OK", "remotive": "Remotive", "himalayas": "Himalayas",
    "weworkremotely": "We Work Remotely", "workingnomads": "Working Nomads",
    "nodesk": "NoDesk", "hackernews": "HN Who is hiring", "linkedin": "LinkedIn",
    "indeed": "Indeed", "glassdoor": "Glassdoor", "google": "Google Jobs",
    "builtin": "Built In",
}

_WORKLOAD_EMOJI = {"relaxed": "🟢", "moderate": "🟡", "intense": "🔴"}


def source_label(source: str) -> str:
    if source.startswith("ats:") or source in ("greenhouse", "lever", "ashby"):
        return "company careers page"
    return SOURCE_NAMES.get(source, source)


def _fmt_salary(job: Job) -> str:
    if job.salary_min and job.salary_max:
        return f"{job.salary_min:,}–{job.salary_max:,} {job.salary_currency}"
    if job.sort_salary:
        return f"~{job.sort_salary:,} {job.salary_currency or 'USD'}"
    return "salary n/a"


def build_digest(filtered: list[Job], stats: dict) -> str:
    if not filtered:
        return (f"remote-jobs-digest: 0 matching jobs today "
                f"(out of {stats['total']} collected).")
    top = filtered[:config.DIGEST_TOP_N]
    lines = [
        f"💼 *Remote jobs digest* — {stats['filtered']} match, "
        f"🆕 {stats.get('new_filtered', 0)} new "
        f"(out of {stats['total']} collected)",
        f"🚫 {stats['mgmt_dropped']} management/staff+ · "
        f"🏢 {stats['consulting_dropped']} consultancy · "
        f"⚠️ {stats['revisar']} to review",
        "",
    ]
    for i, j in enumerate(top, 1):
        title = (("🆕 " if j.is_new else "")
                 + ("⚡ " if j.easy_apply else "") + j.title[:54])
        company = (j.company or "?")[:28]
        lvl = (j.ai_level or j.level or "?").upper()
        meta = f"{lvl} · stack {j.stack_score}/10"
        if j.ai_fit is not None:
            meta += f" · fit {j.ai_fit}/10"
        if j.has_exclusivity:
            meta += " · ⚠️exclusivity"
        if j.ai_workload:
            meta += f" {_WORKLOAD_EMOJI.get(j.ai_workload, '')}workload {j.ai_workload}"
        lines.append(f"{i}. *{title}* — {company}")
        lines.append(f"   [{meta}] · 💰 {_fmt_salary(j)}")
        if j.ai_reason:
            lines.append(f"   💬 {j.ai_reason}")
        lines.append(f"   {j.url} (via {source_label(j.source)})")
    return "\n".join(lines)[:3900]


def write_digest(msg: str) -> Path:
    path = Path(config.OUTPUT_DIR) / "digest_latest.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(msg + "\n", encoding="utf-8")
    return path


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_telegram(msg: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; skipping Telegram.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg,
               "parse_mode": "Markdown", "disable_web_page_preview": True}
    try:
        with httpx.Client(timeout=30) as c:
            r = c.post(url, json=payload)
            if r.status_code != 200:  # retry as plain text if Markdown is rejected
                payload.pop("parse_mode")
                r = c.post(url, json=payload)
            r.raise_for_status()
    except httpx.HTTPError as exc:
        log(f"Telegram failed: {type(exc).__name__}: {exc}")
        return False
    log("digest sent to Telegram")
    return True


def send_raw(msg: str) -> bool:
    """Send a free-form message (used by the apply extra's gmail watcher)."""
    return send_telegram(msg)
