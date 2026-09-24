#!/usr/bin/env python3
"""Verificador LLM de candidaturas pendientes — poda la cola antes de rellenar.

El clasificador de keywords tiene techo: "Remote" en el campo ubicación con un
"must be based in the US" enterrado en el párrafo 7, seniority que solo se ve
en las responsabilidades, híbridos que no dicen "hybrid". El coste de esos
falsos positivos se paga a ~2 min por formulario abierto en vano.

Este módulo hace la pregunta de verdad, con la descripción COMPLETA:

    ¿Puede alguien con TU perfil (dónde vives, modalidades, banda de años,
    ver config.yaml) trabajar este puesto? ¿O exige residir en otro país,
    ir a oficina, o un nivel senior/staff?

Veredictos:
    KILL    → estado 'descartada' en el tracker (fill la salta) + motivo
    OK      → se queda en la cola
    UNCLEAR → se queda, marcada para revisar con ojo

Uso:
    ./rjs verify              # verifica todas las 'pendiente' del tracker
    ./rjs verify --dry-run    # muestra veredictos sin tocar el tracker
"""

from __future__ import annotations

from remote_jobs_digest import paths

import argparse
import csv
import dataclasses
import json
import os
import re
import sys
from datetime import date

from remote_jobs_digest import config
from remote_jobs_digest import ai_filter
from remote_jobs_digest.sources.base import Job, log

TRACKER = str(paths.applications_dir() / "tracker.csv")
KILL_LOG = str(paths.applications_dir() / "descartadas.md")
RAW = os.path.join(config.OUTPUT_DIR, "jobs_raw_latest.json")

def build_verify_system() -> str:
    """The verifier prompt, written from your profile (where you live, the
    work modes you accept, your years band) instead of a fixed candidate."""
    p = config.PROFILE
    home = ", ".join(p.geo.home_hints[:3]) or "their home country"
    region = ", ".join(p.geo.region_hints[:4]) or "their region"
    modes = ", ".join(sorted(p.geo.accept_modes)) or "remote"
    exp = p.experience
    lang = os.getenv("RJS_AI_LANGUAGE", "English")
    return f"""You verify job postings for one specific candidate. Read the FULL \
description and reply ONLY with valid JSON.

THE CANDIDATE lives in {home}. Accepts: {modes} (onsite/hybrid only near \
home). Ideal band {exp.ideal_min}-{exp.ideal_max} years of experience. Will \
not relocate.

Profile:
{p.thesis()}

Return: {{"verdict": "OK" | "KILL" | "UNCLEAR", "reason": "<max 15 words, in \
{lang}>"}}

KILL if ANY of these holds:
- It requires residing in, holding work authorization for, or being "based \
in" a country or region that does not include {home} or {region}. Phrases \
like "must be located in", "authorized to work in the US", "only available to \
candidates in...", lists of US states, "W-2".
- It is onsite or hybrid away from {home}.
- It asks for more than {exp.max_required} years, or is clearly \
senior/staff/principal/lead by responsibilities (owning architecture, \
mentoring, leading).
- It is a management, sales, support or consultancy/outsourcing role.

OK if: remote compatible with living in {home} AND the level is reachable \
AND it is engineering.

UNCLEAR if the description does not let you decide. When torn between OK and \
KILL on geography, UNCLEAR: a human will look. But an explicit restriction \
phrase means KILL."""


def load_jobs_by_url() -> dict[str, Job]:
    if not os.path.exists(RAW):
        sys.exit(f"No hay run previo en {RAW}")
    keep = {f.name for f in dataclasses.fields(Job)}
    out: dict[str, Job] = {}
    with open(RAW, encoding="utf-8") as f:
        for d in json.load(f):
            j = Job(**{k: v for k, v in d.items() if k in keep})
            out[j.url] = j
    return out


def verify_one(job: Job) -> tuple[str, str]:
    """(verdict, reason) para una oferta, leyendo la descripción completa."""
    body = {
        "model": config.AI_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": build_verify_system()},
            {"role": "user", "content":
                f"Título: {job.title}\nEmpresa: {job.company}\n"
                f"Ubicación declarada: {job.location}\n\n"
                f"Descripción:\n{ai_filter._clean(job.description, 6000)}"},
        ],
    }
    # post_for_writing prueba Gemini primero (sin usar `key`) y solo cae a
    # Groq con esta clave si Gemini falla o no hay key de Gemini. Pasar ""
    # aquí (como hacía antes) rompe silenciosamente ESE fallback: cualquier
    # respuesta con Groq vuelve UNCLEAR por "no parseable" — el filtro de
    # pre-vuelo queda inerte sin que nada lo avise.
    content = ai_filter.post_for_writing(ai_filter._resolve_key(), body) or ""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return "UNCLEAR", "respuesta LLM no parseable"
    try:
        data = json.loads(m.group(0))
        v = str(data.get("verdict", "UNCLEAR")).upper()
        if v not in ("OK", "KILL", "UNCLEAR"):
            v = "UNCLEAR"
        return v, str(data.get("reason", ""))[:90]
    except Exception:  # noqa: BLE001
        return "UNCLEAR", "JSON inválido del LLM"


def main() -> None:
    ap = argparse.ArgumentParser(description="Poda la cola de pendientes con LLM")
    ap.add_argument("--dry-run", action="store_true",
                    help="mostrar veredictos sin tocar el tracker")
    args = ap.parse_args()

    if not os.path.exists(TRACKER):
        sys.exit("No hay tracker.")
    with open(TRACKER, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cols = list(rows[0].keys())
    pending = [r for r in rows if r.get("estado") == "pendiente"]
    if not pending:
        sys.exit("No hay pendientes que verificar.")
    if not ai_filter.has_llm():
        sys.exit("Sin clave LLM (GROQ_API_KEY o GEMINI_API_KEY).")

    by_url = load_jobs_by_url()
    log(f"Verificando {len(pending)} pendientes con la descripción completa...")

    kills: list[tuple[dict, str]] = []
    unclear = ok = 0
    for i, r in enumerate(pending, 1):
        job = by_url.get(r["url"])
        if job is None:
            log(f"  [{i}] · sin descripción en el último run: {r['empresa'][:30]}")
            continue
        verdict, reason = verify_one(job)
        mark = {"OK": "✓", "KILL": "✗", "UNCLEAR": "?"}[verdict]
        log(f"  [{i}/{len(pending)}] {mark} {r['empresa'][:22]:22} "
            f"{r['puesto'][:38]:38} — {reason}")
        if verdict == "KILL":
            kills.append((r, reason))
        elif verdict == "UNCLEAR":
            unclear += 1
        else:
            ok += 1

    print(f"\n  ✓ OK {ok} · ? UNCLEAR {unclear} · ✗ KILL {len(kills)}")
    if args.dry_run or not kills:
        if args.dry_run:
            print("  (dry-run: tracker intacto)")
        return

    killed_urls = {r["url"] for r, _ in kills}
    # Releer el tracker justo antes de escribir: la verificación tarda minutos
    # y el usuario puede estar marcando 'm' en autofill en paralelo — escribir
    # las filas leídas al principio pisaría sus marcas.
    with open(TRACKER, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cols = list(rows[0].keys())
    for r in rows:
        if r["url"] in killed_urls and r.get("estado") != "enviada":
            r["estado"] = "descartada"
    with open(TRACKER, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with open(KILL_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n## Verificación {date.today().isoformat()}\n\n")
        for r, reason in kills:
            f.write(f"- **{r['empresa']}** — {r['puesto'][:60]}: {reason}\n")
    print(f"  Tracker actualizado: {len(kills)} descartadas "
          f"(motivos en applications/descartadas.md)")
    print(f"  Cola restante para ./rjs fill: {ok + unclear}")


if __name__ == "__main__":
    main()
