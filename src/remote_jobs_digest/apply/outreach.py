#!/usr/bin/env python3
"""Cold outreach 1:1 — correos a empresas SIN oferta publicada.

Genera, por empresa, un correo corto y personalizado (idioma de la empresa,
datos reales del perfil, un gancho específico de SU negocio) listo para
revisar y enviar desde tu Gmail. Nada se envía desde aquí: el output son
borradores en `outreach/` + un CSV de contactos y estado.

Por qué borradores y no envío directo:
  - un correo frío mal dirigido quema la empresa para siempre;
  - los contactos buenos (un EM o un tech lead concreto) se encuentran a mano
    en LinkedIn — el script deja huecos para añadirlos;
  - el envío 1:1 espaciado desde tu Gmail real llega a inbox; un batch SMTP
    con volumen huele a spam y acaba en Promotions o bloqueado.

Uso:
    ./rjs outreach --spain --top 10          # empresas que contratan en España
    ./rjs outreach --company Wallapop        # una concreta
    ./rjs outreach --list                    # ver candidatas sin generar nada

Cada carpeta `outreach/<empresa>/` contiene `email.md` (asunto + cuerpo + a
quién) y se registra en `outreach/contacts.csv`.
"""

from __future__ import annotations

from remote_jobs_digest import paths

import argparse
import csv
import json
import os
import re
import sys
from datetime import date
from urllib.parse import urlparse

from remote_jobs_digest import config
from remote_jobs_digest import ai_filter

COMPANIES = str(paths.boards_dir() / "companies.json")
PROFILE = str(paths.narrative_file())
OUT_DIR = str(paths.outreach_dir())
CONTACTS = os.path.join(OUT_DIR, "contacts.csv")

CONTACT_COLS = ["empresa", "dominio", "email", "persona", "rol",
                "estado", "generado", "enviado", "respuesta"]

EMAIL_SYSTEM = """Redactas un correo FRÍO de un ingeniero a una empresa donde \
NO hay oferta publicada. El objetivo: que un humano ocupado lo lea entero y \
conteste. Escribes COMO ÉL (su voz real, ver perfil).

REGLAS INNEGOCIABLES — el correo no puede oler a generado ni a plantilla:
- MÁXIMO 130 palabras de cuerpo. Los correos largos de desconocidos no se leen.
- Un (1) gancho específico de ESTA empresa en la primera frase: su producto, \
su stack conocido, algo de su descripción. Si el gancho valdría para otra \
empresa, no es un gancho.
- Después: 2-3 frases de lo que él ha construido QUE LE SIRVA a esta empresa, \
con una o dos cifras reales del perfil. No más de dos cifras: una lista de \
métricas huele a CV pegado.
- Cierre: una petición concreta y pequeña ("¿tenéis 15 minutos esta semana?" \
/ "if you're planning to hire backend/AI this year, I'd love a short call"). \
Nada de "quedo a su disposición".
- PROHIBIDO: "I hope this email finds you well", "me pongo en contacto", \
"passionate", "excited", "I came across your company", "reach out", \
"perfect fit", "valuable contribution", emojis, negritas, listas con viñetas.
- Sin adulación ("me encanta lo que hacéis"). El gancho demuestra que conoce \
la empresa; decirlo explícitamente lo estropea.
- Idioma: el de la empresa (español si es española, inglés en otro caso).
- Firma solo con el nombre. Los enlaces (GitHub, LinkedIn) van aparte, no
  incrustados en frases de venta.
- Varía la estructura entre correos: no empieces dos correos igual.

Devuelve SOLO JSON válido:
{"subject": "<asunto de 4-8 palabras, minúscula natural, sin clickbait>",
 "body": "<el cuerpo, con \\n\\n entre párrafos, terminando en la firma: solo \
el nombre de pila del candidato, tal y como aparece en el perfil>"}"""


def load_profile() -> str:
    with open(PROFILE, encoding="utf-8") as f:
        return f.read()


def load_companies() -> list[dict]:
    with open(COMPANIES, encoding="utf-8") as f:
        return json.load(f)


def domain_of(c: dict) -> str:
    for u in (c.get("website"), c.get("careers_url")):
        if u:
            host = urlparse(u).netloc.lower().removeprefix("www.")
            if host and "." in host:
                return host
    return ""


def pick_targets(args, companies: list[dict]) -> list[dict]:
    done: set[str] = set()
    if os.path.exists(CONTACTS):
        with open(CONTACTS, encoding="utf-8") as f:
            done = {r["empresa"].lower() for r in csv.DictReader(f)}
    sel = []
    for c in companies:
        if c["name"].lower() in done and not args.company:
            continue
        if c.get("is_consulting"):
            continue
        # Las consultoras conocidas no llevan flag en el dataset: van por nombre.
        if any(k in c["name"].lower() for k in config.CONSULTING_COMPANIES):
            continue
        if args.company:
            if args.company.lower() in c["name"].lower():
                sel.append(c)
            continue
        if args.spain and not c.get("hires_spain"):
            continue
        if not args.spain and c.get("region") not in (
                "worldwide", "europe", "americas-europe", "spain"):
            continue
        if c.get("stack_score", 0) < args.min_stack:
            continue
        sel.append(c)
    sel.sort(key=lambda c: (-c.get("stack_score", 0),
                            not c.get("hires_spain")))
    return sel[:args.top] if not args.company else sel[:3]


def generate_email(profile: str, c: dict) -> dict | None:
    techs = ", ".join(c.get("technologies", [])[:10])
    ctx = (f"Empresa: {c['name']}\n"
           f"Web: {c.get('website','')}\n"
           f"A qué se dedica: {c.get('business') or '(no consta; dedúcelo del nombre/web con prudencia, sin inventar detalles)'}\n"
           f"Tecnologías conocidas: {techs or '(no constan)'}\n"
           f"¿Contrata en España con contrato local?: {'sí' if c.get('hires_spain') else 'no consta'}\n")
    body = {
        "model": config.AI_MODEL,
        "temperature": 0.65,
        "messages": [
            {"role": "system", "content": EMAIL_SYSTEM},
            {"role": "user", "content":
                f"PERFIL DEL CANDIDATO:\n{profile}\n\n---\n"
                f"EMPRESA OBJETIVO:\n{ctx}"},
        ],
    }
    content = ai_filter.post_for_writing("", body) or ""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if data.get("subject") and data.get("body"):
            return data
    except Exception:  # noqa: BLE001
        pass
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def write_draft(c: dict, mail: dict) -> str:
    folder = os.path.join(OUT_DIR, _slug(c["name"]))
    os.makedirs(folder, exist_ok=True)
    dom = domain_of(c)
    with open(os.path.join(folder, "email.md"), "w", encoding="utf-8") as f:
        f.write(f"""# Cold email — {c['name']}

> **Revisa antes de enviar.** Sobre todo el gancho de la primera frase: si es
> genérico o inexacto, reescríbelo — es lo que decide si contestan.
> Envío recomendado: desde tu Gmail, 1:1, máx. 10-15/día, nunca en CC.

## A quién

| Contacto | Email | Fuente |
|---|---|---|
| (buscar en LinkedIn: eng manager / CTO / talent) | | manual |
| Genérico | {'careers@' + dom if dom else '(sin dominio)'} | adivinado — puede rebotar |

## Asunto

{mail['subject']}

## Cuerpo

{mail['body']}

---
_Adjuntar:_ tu CV en PDF (ver RJS_CV_PATH)
_Enlaces bajo la firma:_ tu GitHub · tu LinkedIn
""")
    return folder


def append_contacts(c: dict, folder: str) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    exists = os.path.exists(CONTACTS)
    with open(CONTACTS, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CONTACT_COLS)
        if not exists:
            w.writeheader()
        dom = domain_of(c)
        w.writerow({"empresa": c["name"], "dominio": dom,
                    "email": f"careers@{dom}" if dom else "",
                    "persona": "", "rol": "", "estado": "borrador",
                    "generado": date.today().isoformat(),
                    "enviado": "", "respuesta": ""})


def main() -> None:
    ap = argparse.ArgumentParser(description="Cold outreach 1:1 (borradores)")
    ap.add_argument("--spain", action="store_true",
                    help="solo empresas que contratan en España")
    ap.add_argument("--company", help="una empresa concreta (subcadena)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--min-stack", type=int, default=4,
                    help="stack_score mínimo de la empresa (0-10)")
    ap.add_argument("--list", action="store_true",
                    help="listar candidatas sin generar correos")
    args = ap.parse_args()

    companies = load_companies()
    targets = pick_targets(args, companies)
    if not targets:
        sys.exit("Ninguna empresa candidata con esos filtros.")
    if args.list:
        for c in targets:
            print(f"  {c.get('stack_score',0):>2} "
                  f"{'ES' if c.get('hires_spain') else '  '} "
                  f"{c['name'][:30]:30} {domain_of(c):28} "
                  f"{(c.get('business') or '')[:38]}")
        return
    if not ai_filter.has_llm():
        sys.exit("Sin clave LLM (GROQ_API_KEY o GEMINI_API_KEY).")

    profile = load_profile()
    ok = 0
    for i, c in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {c['name']}", file=sys.stderr)
        mail = generate_email(profile, c)
        if not mail:
            print("   · sin correo (LLM no devolvió JSON)", file=sys.stderr)
            continue
        folder = write_draft(c, mail)
        append_contacts(c, folder)
        ok += 1
        print(f"   → {folder}/email.md", file=sys.stderr)

    print(f"\n  ✅ {ok} borradores en outreach/")
    print(f"  📋 contactos y estado: {CONTACTS}")
    print("\n  Siguiente paso: añade 1-2 personas reales por empresa (LinkedIn)")
    print("  al email.md, revisa el gancho, y envía desde tu Gmail — 1:1,")
    print("  10-15 al día como mucho.")


if __name__ == "__main__":
    main()
