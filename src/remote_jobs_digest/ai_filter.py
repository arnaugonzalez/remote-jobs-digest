"""Optional LLM pass over the jobs that survived the deterministic filters.

Reads each candidate's DESCRIPTION and returns, per batch:
  - level    : junior / mid / senior (the REAL level from responsibilities and
               years asked, not from the title)
  - fit      : 0-10 against your profile (config.yaml + profile.md)
  - workload : relaxed / moderate / intense / unknown
  - reason   : one short line

Any OpenAI-compatible endpoint works (OpenAI, Groq, OpenRouter, DeepSeek,
Gemini's compat endpoint, Ollama, LM Studio):

    RJS_LLM_BASE_URL  default https://api.groq.com/openai/v1
    RJS_LLM_API_KEY   (GROQ_API_KEY is accepted as an alias)
    RJS_LLM_MODEL     default llama-3.3-70b-versatile

An optional fallback endpoint (RJS_LLM_FALLBACK_BASE_URL / _API_KEY / _MODEL)
is tried when the primary is rate-limited. Setting only GEMINI_API_KEY makes
Gemini's compat endpoint the fallback.

If anything fails the jobs keep flowing unscored: the LLM never breaks a run.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from datetime import datetime, timezone

import httpx

from remote_jobs_digest import config
from remote_jobs_digest.sources.base import Job, log

AI_CACHE_PATH = os.path.join(config.OUTPUT_DIR, "ai_scores_cache.json")

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _endpoint(prefix: str) -> tuple[str, str, str]:
    """(base_url, api_key, model) for RJS_LLM_* or RJS_LLM_FALLBACK_*."""
    if prefix == "RJS_LLM":
        base = os.getenv("RJS_LLM_BASE_URL", DEFAULT_BASE_URL)
        key = os.getenv("RJS_LLM_API_KEY") or os.getenv("GROQ_API_KEY", "")
        model = config.AI_MODEL
    else:
        base = os.getenv("RJS_LLM_FALLBACK_BASE_URL", "")
        key = os.getenv("RJS_LLM_FALLBACK_API_KEY", "")
        model = os.getenv("RJS_LLM_FALLBACK_MODEL", "")
        if not base and os.getenv("GEMINI_API_KEY"):
            base, key = GEMINI_BASE_URL, os.environ["GEMINI_API_KEY"]
            model = model or "gemini-2.5-flash"
    return base.rstrip("/"), key, model


def _is_local(base: str) -> bool:
    return any(h in base for h in ("localhost", "127.0.0.1", "0.0.0.0"))


def _configured(prefix: str) -> bool:
    base, key, model = _endpoint(prefix)
    return bool(base and model and (key or _is_local(base)))


def _resolve_key() -> str:
    """Primary API key ('' for keyless local endpoints). Kept for callers."""
    return _endpoint("RJS_LLM")[1]


def _gemini_key() -> str:
    """Kept for callers: truthy when a fallback endpoint is usable."""
    return "fallback" if _configured("RJS_LLM_FALLBACK") else ""


def has_llm() -> bool:
    """Is any LLM endpoint usable?"""
    return _configured("RJS_LLM") or _configured("RJS_LLM_FALLBACK")


def _level_rules() -> str:
    p = config.PROFILE
    exp, levels = p.experience, sorted(p.role.allowed_levels - {"unknown"})
    lo = exp.ideal_max + 1
    stretch = f"{lo}-{exp.stretch}" if exp.stretch > lo else str(lo)
    stacks = ", ".join(
        f"{c.label} ({', '.join(c.strong[:6])})"
        for c in sorted(p.stack.weighted, key=lambda c: -c.weight)[:4]
    ) or "any"
    return (
        f"- \"fit\": integer 0-10. 10 = a role whose MAIN stack is one of "
        f"[{stacks}], at level {' or '.join(levels) or 'any'}, asking for "
        f"{exp.ideal_min}-{exp.ideal_max} years. HARD RULES:\n"
        f"  * If the main stack is none of those, fit MUST be <= 3.\n"
        f"  * If the level is outside [{', '.join(levels) or 'any'}] or it asks "
        f"for more than {exp.max_required} years, fit MUST be <= 2.\n"
        f"  * If it asks for {stretch} years, fit is at most 6: defensible, "
        f"not ideal."
    )


def build_system_prompt() -> str:
    lang = os.getenv("RJS_AI_LANGUAGE", "English")
    return f"""You evaluate job postings for one specific candidate. Their \
profile (the fit criterion):

{config.USER_THESIS}

For each posting, READ the description and return:
- "level": the REAL level from responsibilities and years asked, not from the \
title. One of "junior", "mid", "senior". Leading teams, owning architecture, \
6+ years or mentoring others means "senior".
{_level_rules()}
- "workload": "relaxed", "moderate", "intense" or "unknown" from workload \
signals (on-call, "fast-paced", "wear many hats", chaotic startup = intense).
- "reason": at most 12 words, in {lang}.

Reply ONLY with valid JSON: {{"results": [{{"i": <index>, "level": ..., \
"fit": ..., "workload": ..., "reason": ...}}, ...]}}. One object per posting, \
using the index "i" you were given."""


def _clean(text: str, limit: int) -> str:
    text = html.unescape(_TAG_RE.sub(" ", text or ""))
    return _WS_RE.sub(" ", text).strip()[:limit]


def _post(prefix: str, body: dict, timeout: float = 90) -> httpx.Response:
    base, key, model = _endpoint(prefix)
    body = dict(body, model=model)
    if base.startswith(GEMINI_BASE_URL):
        # Gemini rejects response_format json_object on some models; the prompt
        # already demands JSON.
        body.pop("response_format", None)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    with httpx.Client(timeout=timeout) as c:
        return c.post(f"{base}/chat/completions", headers=headers, json=body)


def _post_fallback(body: dict) -> str | None:
    if not _configured("RJS_LLM_FALLBACK"):
        return None
    r = _post("RJS_LLM_FALLBACK", body)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def post_for_writing(key: str, body: dict) -> str | None:
    """Used by the apply extra to WRITE text (letters, answers). Same transport
    as scoring; `key` is accepted for backwards compatibility and ignored."""
    return _post_with_backoff(key, body)


def _post_with_backoff(key: str, body: dict, retries: int = 4) -> str | None:
    """POST to the primary endpoint, retrying on 429, then the fallback."""
    if not _configured("RJS_LLM"):
        return _post_fallback(body)
    for attempt in range(retries + 1):
        r = _post("RJS_LLM", body, timeout=60)
        if r.status_code == 429 and attempt < retries:
            wait = float(r.headers.get("retry-after", 2 ** attempt))
            wait = min(wait + 0.5, 30.0)
            log(f"  429: waiting {wait:.1f}s (Retry-After)")
            time.sleep(wait)
            continue
        if r.status_code == 429 and _configured("RJS_LLM_FALLBACK"):
            log("  primary LLM exhausted -> fallback endpoint")
            try:
                return _post_fallback(body)
            except httpx.HTTPError as exc:
                log(f"  fallback failed too: {type(exc).__name__}")
                return None
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    return None


def _score_batch(key: str, batch: list[Job]) -> None:
    payload = []
    for i, j in enumerate(batch):
        payload.append({
            "i": i,
            "title": j.title,
            "company": j.company,
            "location": j.location,
            "title_level_hint": j.level,
            "description": _clean(j.description, config.AI_DESC_CHARS),
        })
    body = {
        "model": config.AI_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    content = _post_with_backoff(key, body)
    if content is None:
        return
    results = json.loads(content).get("results", [])
    by_index = {item.get("i"): item for item in results if isinstance(item, dict)}
    for i, job in enumerate(batch):
        item = by_index.get(i)
        if not item:
            continue
        job.ai_level = str(item.get("level", "")).lower().strip()
        try:
            job.ai_fit = max(0, min(10, int(item.get("fit"))))
        except (TypeError, ValueError):
            job.ai_fit = None
        job.ai_workload = str(item.get("workload", "")).lower().strip()
        job.ai_reason = str(item.get("reason", "")).strip()[:120]


def _load_ai_cache() -> dict:
    if os.path.exists(AI_CACHE_PATH):
        try:
            with open(AI_CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001 — caché corrupta: se regenera
            return {}
    return {}


def _save_ai_cache(cache: dict) -> None:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    with open(AI_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def _apply_cached_score(job: Job, entry: dict) -> None:
    job.ai_level = entry.get("ai_level")
    job.ai_fit = entry.get("ai_fit")
    job.ai_workload = entry.get("ai_workload")
    job.ai_reason = entry.get("ai_reason")


def score_jobs(jobs: list[Job]) -> bool:
    """Puntúa hasta AI_MAX_JOBS ofertas in-place. Devuelve True si corrió.

    Cachea por Job.id en AI_CACHE_PATH: una oferta que ya se puntuó ayer (y
    sigue en el pool de candidatos hoy) no se vuelve a mandar a Groq/Gemini.
    RJS_AI_REFRESH=1 ignora la caché (útil tras cambiar USER_THESIS o el
    prompt)."""
    if not config.USE_AI_FILTER:
        return False
    key = _resolve_key()
    if not has_llm():
        log("No LLM endpoint configured (RJS_LLM_API_KEY); skipping the AI pass.")
        return False
    pool = jobs[:config.AI_MAX_JOBS]
    if not pool:
        return False

    cache = {} if os.getenv("RJS_AI_REFRESH") == "1" else _load_ai_cache()
    to_score = []
    n_cached = 0
    for job in pool:
        entry = cache.get(job.id)
        if entry:
            _apply_cached_score(job, entry)
            n_cached += 1
        else:
            to_score.append(job)
    if n_cached:
        log(f"IA: {n_cached} ofertas ya puntuadas (caché)")
    if not to_score:
        return n_cached > 0

    log(f"IA ({config.AI_MODEL}): puntuando {len(to_score)} ofertas...")
    n = config.AI_BATCH_SIZE
    scored = 0
    n_batches = (len(to_score) + n - 1) // n
    for idx, start in enumerate(range(0, len(to_score), n)):
        batch = to_score[start:start + n]
        try:
            _score_batch(key, batch)
            scored += len(batch)
            now = datetime.now(timezone.utc).isoformat()
            for job in batch:
                cache[job.id] = {
                    "ai_level": job.ai_level, "ai_fit": job.ai_fit,
                    "ai_workload": job.ai_workload, "ai_reason": job.ai_reason,
                    "scored_at": now,
                }
            _save_ai_cache(cache)  # persistido tras cada lote: un fallo a
            # medias no pierde lo ya puntuado ese mismo run.
        except Exception as exc:  # noqa: BLE001 — la IA nunca tumba el run
            log(f"  lote {idx + 1}/{n_batches} falló: {exc}")
        if idx + 1 < n_batches:    # pequeño respiro entre lotes (rate limit)
            time.sleep(config.AI_BATCH_DELAY)
    log(f"IA: {scored} ofertas puntuadas")
    return scored > 0 or n_cached > 0
