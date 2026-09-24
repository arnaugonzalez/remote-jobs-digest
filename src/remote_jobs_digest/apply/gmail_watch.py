#!/usr/bin/env python3
"""Lee Gmail 2x/día, clasifica con LLM y actualiza el tracker de candidaturas.

Por qué IMAP + App Password y no la API de Gmail OAuth: para un cron headless
en el VPS no hay navegador para el consentimiento, y el acceso MCP que usa
Claude Code en sesión interactiva no es scriptable fuera de la conversación.
Una App Password de Google (myaccount.google.com/apppasswords, 2FA requerido)
da acceso IMAP de solo-lectura sin flujo OAuth. Coste del LLM: irrelevante —
~25k tokens/día, un par de céntimos al mes con cualquier proveedor barato; se
reutiliza el mismo Gemini/Groq ya montado en ai_filter.py, sin añadir una
tercera clave.

Qué hace:
    1. IMAP: trae correos de las últimas HOURS_BACK horas (no marca como leído).
    2. Filtra a remitentes de ATS conocidos (Greenhouse, Ashby, Workable...) o
       remitentes/asuntos que casan con una empresa del tracker.
    3. Cada correo relevante se clasifica con el LLM: confirmación / rechazo /
       entrevista / oferta / otro — y se cruza contra applications/tracker.csv
       por nombre de empresa para actualizar 'respuesta' (nunca 'estado': si
       marca error, el 'enviada' original no se toca).
    4. Aparte, un segundo paso barre TODA la bandeja (no solo ATS) buscando
       correos "importantes" no relacionados con candidaturas (ej. una oferta
       directa de reclutador, un cliente freelance) — están señalados como
       tal desde 'nada estructural que buscar', así que es un juicio del LLM.
    5. Digest a Telegram con lo encontrado. Si no hay nada nuevo, no manda nada
       (un cron que avisa en vano dos veces al día se acaba ignorando).

Uso:
    ./rjs gmail                # todo el flujo
    ./rjs gmail --dry-run       # no toca el tracker ni envía Telegram
    ./rjs gmail --hours 24      # ventana de lectura (default 14h, pensado
                                 # para 2 pasadas/día con margen de solape)

Config (.env):
    GMAIL_ADDRESS=tu@gmail.com
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx      (16 chars, con o sin espacios)
"""

from __future__ import annotations

from remote_jobs_digest import paths

import argparse
import csv
import email
import imaplib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime

from remote_jobs_digest import config
from remote_jobs_digest import ai_filter
from remote_jobs_digest import notify
from remote_jobs_digest.sources.base import log

TRACKER = str(paths.applications_dir() / "tracker.csv")
STATE_PATH = os.path.join(config.OUTPUT_DIR, "gmail_watch_state.json")

IMAP_HOST = "imap.gmail.com"
HOURS_BACK_DEFAULT = 14

# Remitentes de ATS conocidos — cualquier correo de estos dominios es, con
# certeza razonable, sobre una candidatura (confirmación, rechazo, oferta...).
ATS_SENDER_HINTS = [
    "greenhouse-mail.io", "greenhouse.io", "ashbyhq.com", "workablemail.com",
    "workable.com", "lever.co", "smartrecruiters.com", "breezy.hr",
    "personio.de", "personio.com", "recruitee.com", "teamtailor.com",
    "myworkday.com", "icims.com", "jobvite.com",
]

CLASSIFY_SYSTEM = """Clasificas un correo recibido por un candidato que está \
buscando trabajo remoto (backend Python / AI engineering). Devuelve SOLO JSON:

{"is_job_related": true|false,
 "company": "<nombre de la empresa, o \\"\\" si no aplica>",
 "category": "confirmacion" | "rechazo" | "entrevista" | "oferta_trabajo" |
             "codigo_verificacion" | "otro_candidatura" | "no_relacionado",
 "summary": "<una frase en español, máx 20 palabras, qué dice el correo>",
 "important": true|false}

Categorías:
- "confirmacion": ATS confirma que la candidatura se recibió/envió.
- "rechazo": dicen que no siguen adelante con el candidato.
- "entrevista": piden agendar una llamada/entrevista, o dan siguiente paso.
- "oferta_trabajo": oferta de empleo formal (compensación, onboarding).
- "codigo_verificacion": código de seguridad para reenviar una solicitud
  (Greenhouse manda esto a veces) — MUY importante, hay que actuar rápido.
- "otro_candidatura": relacionado con una candidatura pero no encaja arriba.
- "no_relacionado": no tiene que ver con la búsqueda de trabajo.

"important"=true si el candidato debería leerlo hoy mismo: entrevista,
código de verificación, oferta de trabajo, o un contacto directo de un
reclutador/empresa fuera del flujo normal de ATS (ese es "otro" importante
aunque is_job_related sea true con category "no_relacionado" si no encaja).
"important"=false para confirmaciones rutinarias y rechazos genéricos."""


def _imap_creds() -> tuple[str, str]:
    addr = os.getenv("GMAIL_ADDRESS", "")
    pw = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not addr or not pw:
        sys.exit("Faltan GMAIL_ADDRESS / GMAIL_APP_PASSWORD en .env")
    return addr, pw


def _decode(raw) -> str:
    if raw is None:
        return ""
    parts = decode_header(raw)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def fetch_recent(hours_back: int) -> list[dict]:
    addr, pw = _imap_creds()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours_back))
    since_str = since.strftime("%d-%b-%Y")   # IMAP SEARCH solo filtra por día

    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    imap.login(addr, pw)
    imap.select("INBOX", readonly=True)
    typ, data = imap.search(None, f'(SINCE "{since_str}")')
    if typ != "OK":
        imap.logout()
        return []
    ids = data[0].split()
    out = []
    for msg_id in ids:
        typ, msg_data = imap.fetch(msg_id, "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        try:
            dt = parsedate_to_datetime(msg.get("Date"))
            if dt and dt < since:
                continue      # SEARCH SINCE es por día completo, filtra fino aquí
        except Exception:  # noqa: BLE001
            pass
        body = _extract_body(msg)
        out.append({
            "id": _decode(msg.get("Message-ID")) or msg_id.decode(),
            "from": _decode(msg.get("From")),
            "subject": _decode(msg.get("Subject")),
            "date": _decode(msg.get("Date")),
            "body": body[:3000],
        })
    imap.logout()
    return out


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and \
                    "attachment" not in str(part.get("Content-Disposition", "")):
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return str(msg.get_payload())


def is_candidate(mail: dict) -> bool:
    """Preselección barata antes de gastar LLM: ATS conocido o alguna empresa
    del tracker mencionada en remitente/asunto."""
    frm = mail["from"].lower()
    if any(h in frm for h in ATS_SENDER_HINTS):
        return True
    subj = mail["subject"].lower()
    companies = _tracker_companies()
    return any(c in frm or c in subj for c in companies if len(c) > 3)


def _tracker_companies() -> set[str]:
    if not os.path.exists(TRACKER):
        return set()
    with open(TRACKER, encoding="utf-8") as f:
        return {re.sub(r"\s*\(.*?\)", "", r["empresa"]).strip().lower()
                for r in csv.DictReader(f) if r.get("empresa")}


def classify(mail: dict) -> dict | None:
    body = {
        "model": config.AI_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": CLASSIFY_SYSTEM},
            {"role": "user", "content":
                f"De: {mail['from']}\nAsunto: {mail['subject']}\n\n"
                f"Cuerpo:\n{mail['body'][:2000]}"},
        ],
    }
    content = ai_filter.post_for_writing("", body) or ""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None


def update_tracker(matches: list[tuple[dict, dict]]) -> int:
    """Escribe 'respuesta' en la fila cuyo nombre de empresa case. NUNCA toca
    'estado' — eso lo decide autofill.py cuando el usuario confirma el envío."""
    if not os.path.exists(TRACKER) or not matches:
        return 0
    with open(TRACKER, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        cols = rows[0].keys() if rows else []
    if "respuesta" not in cols:
        cols = list(cols) + ["respuesta"]
    updated = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for _mail, verdict in matches:
        comp = verdict.get("company", "").strip().lower()
        if not comp:
            continue
        for r in rows:
            r_comp = re.sub(r"\s*\(.*?\)", "", r.get("empresa", "")).strip().lower()
            if comp in r_comp or r_comp in comp:
                note = f"{verdict['category']} {today}"
                if note not in (r.get("respuesta") or ""):
                    r["respuesta"] = (r.get("respuesta", "") + " | " + note).strip(" |")
                    updated += 1
    if updated:
        with open(TRACKER, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cols), extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    return updated


def load_seen() -> set[str]:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:  # noqa: BLE001
            return set()
    return set()


def save_seen(ids: set[str]) -> None:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    # No crece sin límite: solo hace falta recordar lo de los últimos días.
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(ids)[-2000:], f)


def build_digest(interesting: list[tuple[dict, dict]]) -> str:
    order = {"codigo_verificacion": 0, "entrevista": 1, "oferta_trabajo": 2,
            "rechazo": 3, "otro_candidatura": 4, "confirmacion": 5,
            "no_relacionado": 6}
    interesting.sort(key=lambda t: order.get(t[1].get("category", ""), 9))
    lines = [f"📬 *Gmail watch* — {len(interesting)} correo(s) relevantes\n"]
    icons = {"codigo_verificacion": "🔑", "entrevista": "📞",
             "oferta_trabajo": "🎉", "rechazo": "❌", "confirmacion": "✅",
             "otro_candidatura": "📋", "no_relacionado": "❗"}
    for mail, v in interesting:
        icon = icons.get(v.get("category", ""), "•")
        comp = v.get("company") or mail["from"][:30]
        lines.append(f"{icon} *{comp}* — {v.get('category', '?')}")
        lines.append(f"   {v.get('summary', '')[:100]}")
        lines.append(f"   _{mail['subject'][:70]}_\n")
    return "\n".join(lines)[:3900]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=HOURS_BACK_DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not ai_filter.has_llm():
        sys.exit("Sin clave LLM (GROQ_API_KEY o GEMINI_API_KEY).")

    log(f"Leyendo Gmail (últimas {args.hours}h)...")
    mails = fetch_recent(args.hours)
    seen = load_seen()
    new_mails = [m for m in mails if m["id"] not in seen]
    log(f"{len(mails)} correos en ventana, {len(new_mails)} no vistos antes")

    candidates = [m for m in new_mails if is_candidate(m)]
    log(f"{len(candidates)} candidatos a clasificar (ATS conocido o empresa "
        f"del tracker)")

    matches: list[tuple[dict, dict]] = []
    interesting: list[tuple[dict, dict]] = []
    for i, mail in enumerate(candidates, 1):
        v = classify(mail)
        if not v:
            log(f"  [{i}/{len(candidates)}] sin clasificar (LLM falló)")
            continue
        log(f"  [{i}/{len(candidates)}] {v.get('category', '?'):20} "
            f"{v.get('company', '')[:24]} — {mail['subject'][:40]}")
        if v.get("is_job_related") and v.get("company"):
            matches.append((mail, v))
        if v.get("important"):
            interesting.append((mail, v))

    if args.dry_run:
        log(f"\n[dry-run] actualizaría {len(matches)} filas del tracker, "
            f"digest con {len(interesting)} correos")
        return

    n_updated = update_tracker(matches)
    save_seen(seen | {m["id"] for m in mails})

    if interesting:
        notify.send_raw(build_digest(interesting))
        log(f"Digest enviado a Telegram ({len(interesting)} correos)")
    else:
        log("Nada relevante nuevo — sin digest")
    log(f"Tracker: {n_updated} filas actualizadas con respuesta")


if __name__ == "__main__":
    main()
