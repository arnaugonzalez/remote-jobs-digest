#!/usr/bin/env python3
"""Kit de aplicación — prepara el envío de CV con el mínimo trabajo manual.

Por cada oferta seleccionada genera una carpeta en `applications/` con:

    oferta.md      resumen de la oferta + enlace directo para aplicar
    carta.md       carta de presentación personalizada (Groq)
    formulario.md  las preguntas REALES del formulario, con respuestas
                   pre-redactadas para las de screening
    (y una fila en applications/tracker.csv para seguir el estado)

Hasta dónde llega la automatización — y por qué:
  Greenhouse, Lever y Ashby exponen la oferta y el formulario en JSON público,
  así que todo el material se genera solo. Lo que NO existe es un endpoint
  público de envío: `POST /applications` de Greenhouse requiere la API key del
  *empleador*. El submit final se hace en el navegador, pegando lo que ya está
  escrito. Es un copy-paste por oferta, no media hora de redacción.

Uso:
    python apply_kit.py --top 10              # las 10 mejores del último run
    python apply_kit.py --only-ai             # solo roles de AI engineering
    python apply_kit.py --company Twilio      # una empresa concreta
    python apply_kit.py --top 5 --no-ai       # sin generar cartas (más rápido)
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
from datetime import datetime, timezone

from remote_jobs_digest import config
from remote_jobs_digest.classifier import Classifier
from remote_jobs_digest.profile.loader import load_profile
from remote_jobs_digest import ai_filter
from remote_jobs_digest.apply import answer_kit
from remote_jobs_digest.apply.identity import Identity, load_identity
from remote_jobs_digest.sources.base import Job, http_get, log

_CLF = Classifier(load_profile())

APPS_DIR = str(paths.applications_dir())
PROFILE_PATH = str(paths.narrative_file())
TRACKER = os.path.join(APPS_DIR, "tracker.csv")

TRACKER_COLS = ["estado", "empresa", "puesto", "ubicacion", "years", "ai_role",
                "stack", "salario", "url", "carpeta", "generado", "aplicado",
                "respuesta", "ai_fit", "ai_workload", "verify"]

def screening_answers(me: Identity) -> list[tuple[list[str], str]]:
    """(keywords, answer) rules, first match wins; values come from
    identity.yaml, so an unset field leaves the answer blank ("_(pending)_"
    in formulario.md) instead of inventing one. The KEYWORDS and their ORDER
    are the reusable asset."""
    location = ", ".join(x for x in (me.current_location, me.country) if x)
    return [
        # Personal data first so "first name" never falls into another rule.
        (["first name"], me.first_name),
        (["last name", "surname", "family name"], me.last_name),
        (["full name", "your name"], me.full_name),
        (["email"], me.email),
        (["phone", "mobile", "telephone"], me.phone),
        (["resume", "cv", "curriculum"], "Attach your CV as PDF (see RJS_CV_PATH)."),
        (["cover letter", "carta de"], "Paste the contents of carta.md"),
        # Sponsorship BEFORE authorization: its wording often contains "work
        # authorization" ("will you require sponsorship to retain your work
        # authorization"), and answering "Yes" there is an automatic reject.
        (["require sponsorship", "need sponsorship", "visa sponsorship",
          "require visa", "sponsorship to retain", "require company sponsorship"],
         me.needs_sponsorship),
        # Also before authorization: contains "right to work" but asks for the SOURCE.
        (["source of your right", "basis of your right", "source of your work"],
         me.citizenship),
        (["legally authorized", "authorized to work", "right to work",
          "work authorization"], me.work_authorization),
        (["in person", "in our office", "onsite", "on-site", "hybrid",
          "commute", "relocate"], me.remote_policy),
        (["eu citizen", "european union member state", "citizen of the eu",
          "what is your citizenship", "your citizenship", "nationality"],
         me.citizenship),
        (["how did you hear", "where did you hear", "how did you first learn",
          "how did you find"], "Company careers page."),
        (["current company", "current employer", "present employer"], me.current_company),
        (["current title", "current job title", "current role"], me.current_title),
        (["previously been employed", "worked at", "former employee"], "No."),
        (["preferred name", "preferred first name"], me.preferred_name),
        (["which university", "what university", "university did you",
          "name of your university", "educational institution"], me.university),
        (["degree result", "university degree result", "expected result",
          "university grade", "gpa"], me.degree_result),
        (["what degree", "which degree", "field of study", "degree subject",
          "what did you study"], me.degree),
        (["sat score", "sat or equivalent", "national exams", "university "
          "entrance", "entrance exam", "selectividad", "a-level"], me.entrance_exam),
        (["notice period", "when could you start", "availability to start"],
         me.notice_period),
        (["salary expectation", "compensation expectation", "desired salary"],
         me.salary_expectation),
        (["years of experience", "years of professional"], me.years_experience),
        (["located", "currently reside", "country of residence", "where are you based"],
         location),
        (["linkedin"], me.linkedin),
        (["github", "portfolio", "personal website"], me.github or me.portfolio),
        (["pronouns"], me.pronouns),
    ]


CARTA_SYSTEM = """Eres un asistente que redacta cartas de presentación para un \
candidato concreto que busca trabajo remoto.

IDIOMA: escribe SIEMPRE en el mismo idioma del anuncio. El usuario te dirá cuál \
es. Si el anuncio está en inglés, la carta va ENTERA en inglés — no mezcles.

CÓMO ESCRIBE ESTE CANDIDATO (imítalo, es su voz real):
- **Abre por el problema técnico, no por la empresa.** Nunca "me interesa
  vuestra empresa porque...". Empieza por el problema que el puesto resuelve y
  por qué ya lo ha trabajado.
- **Cada afirmación lleva una cifra.** No dice "mejoré el rendimiento": dice
  "p50 de 4,2s a 2,8s". No dice "reduje costes": dice "de ~$800/mes a ~$40/mes".
  Si una frase no tiene número ni decisión concreta, sobra.
- **Nombra el insight explícitamente**, como una conclusión propia. Ejemplos de
  su forma de pensar: "en un producto de IA el coste de inferencia es COGS, no
  una línea de infraestructura"; "fallar rápido y ruidosamente es una feature,
  no un bug"; "la restricción de presupuesto forzó una arquitectura más limpia
  que la que habría elegido con recursos ilimitados". Formula UNO adaptado a
  esta oferta, derivado de su experiencia real. Es lo más distintivo de su voz.
- **Detalles técnicos específicos, no categorías.** No "usé colas de tareas":
  "Celery con acks_late=True para durabilidad". No "monitoricé": "OpenTelemetry
  a Jaeger + Prometheus/Grafana".
- **Admite los huecos en vez de disimularlos.** Si el anuncio pide algo que no
  tiene, lo dice en una frase y sigue. Eso genera más confianza que rellenar.
- Cero autobombo. No se califica a sí mismo ("candidato sólido", "gran
  comunicador"): describe decisiones y resultados y deja que se deduzcan.

ESTRUCTURA: 250-320 palabras, 3-4 párrafos.
- P1: el problema del puesto + qué ha construido él que va de eso, con cifras.
- P2: el insight propio, aplicado a esta oferta.
- P3: stack concreto que se solapa con el anuncio. Aquí, si falta algo
  importante, lo admite en una frase.
- P4: cierre seco — nivel real si viene a cuento, ubicación y huso horario del
  candidato (según el perfil), 100% remoto, preaviso.

REGLAS DURAS:
- Solo hechos del perfil. NO inventes tecnologías, empresas, clientes ni cifras.
- El perfil está en ESPAÑOL. Si la carta va en inglés, REFORMULA sus ideas en
  inglés natural — no traduzcas literalmente. "producto de IA" es "AI product",
  nunca "product of IA". Jamás dejes una palabra en español dentro de una frase
  en inglés.
- El insight va integrado en tu propia frase, NO entrecomillado como una cita.
  Escribir «One key insight I've gained is that "..."» suena a plantilla.
- NUNCA menciones salario, ni el tuyo ni el de la oferta. Eso se habla después.
- No enumeres el stack entero como una lista de la compra: menciona solo lo que
  se solapa con lo que pide ESTE anuncio.
- Si repites una idea con otras palabras en la misma carta, borra una de las dos.
- Si el anuncio pide más años de los que tiene el candidato, NO lo ocultes ni
  lo menciones a la defensiva: dilo en una línea y reencuádralo hacia los
  sistemas propios en producción con métricas.
- No empieces con "I am writing to apply". Entra directo.

PROHIBIDO usar estas fórmulas (o su equivalente en el otro idioma), que son \
relleno y delatan una carta generada:
  "estoy emocionado / I'm excited to", "me hacen un candidato sólido / makes me \
a strong candidate", "estoy listo para asumir desafíos / ready to take on \
challenges", "contribuir significativamente / contribute significantly", \
"tecnología de vanguardia / cutting-edge technology", "capacidad para trabajar \
de forma independiente y en equipo / ability to work independently and in a \
team", "estoy deseando / I look forward to hearing", "excited to discuss", \
"resonates with me", "aligns with my interests", "me hace ilusión".
Cada frase debe aportar un hecho concreto. Si una frase no dice nada que no \
supieran ya, bórrala.

Devuelve SOLO el texto de la carta, sin asunto ni firma."""


_ES_HINTS = (" que ", " para ", " con ", " los ", " las ", " una ", " del ")


def detect_language(text: str) -> str:
    """Idioma del anuncio, para que la carta salga en el mismo. Heurística
    barata: casi todos los anuncios del pipeline están en inglés, así que basta
    con detectar el castellano por sus palabras funcionales."""
    low = f" {text[:1500].lower()} "
    hits = sum(low.count(h) for h in _ES_HINTS)
    return "español" if hits >= 6 else "inglés"


# --- Formularios por ATS ----------------------------------------------------

def _ats_from_url(url: str) -> tuple[str, str, str]:
    """(tipo, slug, job_id) a partir de la URL de la oferta."""
    patterns = [
        ("greenhouse",
         r"(?:job-boards|boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)"),
        ("greenhouse", r"gh_jid=(\d+)()"),        # stripe.com/jobs?gh_jid=
        ("lever", r"jobs\.lever\.co/([^/]+)/([a-f0-9-]+)"),
        ("ashby", r"jobs\.ashbyhq\.com/([^/]+)/([a-f0-9-]+)"),
    ]
    for kind, rx in patterns:
        m = re.search(rx, url)
        if m:
            g = m.groups()
            return (kind, g[0], g[1]) if len(g) > 1 else (kind, g[0], "")
    return ("", "", "")


def fetch_form_questions(job: Job) -> list[dict]:
    """Preguntas reales del formulario. Solo Greenhouse las expone públicamente
    (`?questions=true`); Lever y Ashby no las publican en su posting API."""
    kind, slug, job_id = _ats_from_url(job.url)
    if kind != "greenhouse" or not job_id:
        return []
    # El slug de la URL puede no ser el del board (stripe.com/jobs?gh_jid=...).
    board = slug if slug and not slug.isdigit() else job.source.split(":")[-1]
    try:
        r = http_get(
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}",
            params={"questions": "true"})
        return r.json().get("questions", []) or []
    except Exception as exc:  # noqa: BLE001 — sin formulario seguimos igual
        log(f"  sin formulario para {job.company}: {type(exc).__name__}")
        return []


_IDENTITY: Identity | None = None


def _identity() -> Identity:
    global _IDENTITY
    if _IDENTITY is None:
        _IDENTITY = load_identity()
    return _IDENTITY


def suggest_answer(label: str) -> str:
    """Respuesta pre-redactada para una pregunta de screening, o "".

    El enunciado se normaliza antes de comparar: los ATS cuelan espacios no
    separables (\\xa0) dentro de las frases — Affirm escribe
    "require\\xa0sponsorship\\xa0for employment" — y sin normalizar no casaba
    ninguna keyword justo en la pregunta más crítica del formulario.
    """
    low = re.sub(r"\s+", " ", label.replace("\xa0", " ")).lower().strip()
    for keywords, answer in screening_answers(_identity()):
        if any(k in low for k in keywords):
            return answer
    return ""


# --- Carta de presentación --------------------------------------------------

def _read_profile() -> str:
    if not os.path.exists(PROFILE_PATH):
        log(f"⚠ falta {PROFILE_PATH} — las cartas saldrán genéricas")
        return ""
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return f.read()


def generate_cover_letter(key: str, profile: str, job: Job) -> str:
    desc = ai_filter._clean(job.description, 3000)
    lang = detect_language(desc)
    body = {
        "model": config.AI_MODEL,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": CARTA_SYSTEM},
            {"role": "user", "content":
                f"El anuncio está en {lang.upper()}. "
                f"ESCRIBE LA CARTA ENTERA EN {lang.upper()}.\n\n"
                f"PERFIL DEL CANDIDATO:\n{profile}\n\n"
                f"---\nOFERTA:\n"
                f"Empresa: {job.company}\n"
                f"Puesto: {job.title}\n"
                f"Ubicación: {job.location}\n"
                f"Descripción:\n{desc}"},
        ],
    }
    content = ai_filter.post_for_writing(key, body)
    return (content or "").strip()


# --- Dossier ----------------------------------------------------------------

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "oferta"


def answer_open_questions(key: str, profile: str, job: Job,
                          questions: list[dict]) -> dict[str, str]:
    """Redacta las preguntas de ensayo del formulario ("describe a project
    you're proud of…"), que ninguna plantilla puede cubrir."""
    if not ai_filter.has_llm():
        return {}
    out: dict[str, str] = {}
    for q in questions:
        label = q.get("label", "")
        if suggest_answer(label) or not answer_kit.is_open_question(label):
            continue
        # Un desplegable no se contesta con un párrafo: si el campo tiene
        # opciones cerradas, redactar texto es tinta perdida — hay que elegir.
        if any("select" in (fd.get("type") or "")
               for fd in q.get("fields", [])):
            continue
        try:
            ans = answer_kit.answer_question(
                key, profile, label, company=job.company,
                job_desc=ai_filter._clean(job.description, 1500))
            if ans:
                out[label] = ans
                log(f"    ✎ redactada: {label[:56]}")
        except Exception as exc:  # noqa: BLE001
            log(f"    · sin redactar ({type(exc).__name__}): {label[:44]}")
    return out


def write_dossier(job: Job, questions: list[dict], carta: str,
                  open_answers: dict[str, str] | None = None) -> str:
    open_answers = open_answers or {}
    folder = os.path.join(APPS_DIR,
                          f"{_slug(job.company)}__{_slug(job.title)}")
    os.makedirs(folder, exist_ok=True)

    years = f"{job.years_required} años" if job.years_required else "no especificados"
    salario = job.salary_text or (f"{job.sort_salary:,} {job.salary_currency}"
                                  if job.sort_salary else "no publicado")
    with open(os.path.join(folder, "oferta.md"), "w", encoding="utf-8") as f:
        f.write(f"""# {job.title} — {job.company}

| | |
|---|---|
| **Aplicar en** | {job.url} |
| **Ubicación** | {job.location or 'n/d'} |
| **Años pedidos** | {years} |
| **Salario** | {salario} |
| **Rol AI** | {'sí' if job.is_ai_role else 'no'} |
| **Encaje de stack** | {job.stack_score}/10 ({', '.join(job.stack_hits) or '—'}) |
| **Nivel detectado** | {job.level} |
| **Geografía** | {job.geo} |
| **Veredicto** | {job.verdict} — {job.reason} |
| **Fuente** | {job.source} |

## Descripción

{ai_filter._clean(job.description, 6000)}
""")

    if carta:
        with open(os.path.join(folder, "carta.md"), "w", encoding="utf-8") as f:
            f.write(f"# Carta — {job.company} · {job.title}\n\n"
                    f"> Generada automáticamente. **Léela antes de enviarla** y "
                    f"ajusta lo que suene a plantilla.\n\n{carta}\n")

    if questions:
        lines = [f"# Formulario — {job.company} · {job.title}\n",
                 f"Aplicar en: {job.url}\n",
                 "> Preguntas reales del formulario, sacadas de la API del ATS.\n",
                 "> Las respuestas de screening vienen pre-rellenadas; el resto "
                 "hay que escribirlas.\n"]
        for q in questions:
            label = q.get("label", "")
            req = "**(obligatoria)**" if q.get("required") else "(opcional)"
            types = ", ".join(fd.get("type", "") for fd in q.get("fields", []))
            lines.append(f"\n## {label} {req}\n\n_tipo: {types}_\n")
            opts = []
            for fd in q.get("fields", []):
                for v in (fd.get("values") or [])[:12]:
                    if v.get("label"):
                        opts.append(v["label"])
            if opts:
                lines.append("Opciones: " + " · ".join(opts) + "\n")
            ans = suggest_answer(label)
            if not ans and open_answers.get(label):
                ans = open_answers[label]
            lines.append(f"**Respuesta:** {ans}\n" if ans
                         else "**Respuesta:** _(pendiente)_\n")
        with open(os.path.join(folder, "formulario.md"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines))
    return folder


def update_tracker(rows: list[dict]) -> None:
    """Añade las nuevas y conserva el estado de las que ya estaban."""
    existing: dict[str, dict] = {}
    if os.path.exists(TRACKER):
        with open(TRACKER, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[row.get("url", "")] = row
    for row in rows:
        prev = existing.get(row["url"])
        if prev:                       # no pisar el estado ya anotado a mano
            prev.update({k: v for k, v in row.items()
                         if k not in ("estado", "aplicado")})
        else:
            existing[row["url"]] = row
    os.makedirs(APPS_DIR, exist_ok=True)
    with open(TRACKER, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRACKER_COLS, extrasaction="ignore")
        w.writeheader()
        for row in existing.values():
            w.writerow(row)


# --- Orquestación -----------------------------------------------------------

def load_jobs() -> list[Job]:
    path = os.path.join(config.OUTPUT_DIR, "jobs_raw_latest.json")
    if not os.path.exists(path):
        sys.exit(f"No hay run previo en {path}. Corre antes: ./run.sh --no-telegram")
    keep = {f.name for f in dataclasses.fields(Job)}
    with open(path, encoding="utf-8") as f:
        return [Job(**{k: v for k, v in d.items() if k in keep})
                for d in json.load(f)]


def main() -> None:
    p = argparse.ArgumentParser(description="Prepara dossiers de aplicación")
    p.add_argument("--top", type=int, default=10, help="cuántas ofertas")
    p.add_argument("--only-ai", action="store_true", help="solo roles de AI")
    p.add_argument("--company", help="filtrar por empresa (subcadena)")
    p.add_argument("--location", help="filtrar por ubicación (p.ej. Spain)")
    p.add_argument("--no-ai", action="store_true", help="no generar cartas")
    p.add_argument("--include-revisar", action="store_true",
                   help="incluir REVISAR además de APTA")
    p.add_argument("--urls-from",
                   help="CSV con columna 'url': genera dossiers EXACTAMENTE "
                        "para esas ofertas (p.ej. research/data/pool_roles.csv), "
                        "ignorando el ranking propio")
    p.add_argument("--regen", action="store_true",
                   help="incluir también ofertas ya presentes en el tracker "
                        "(por defecto se saltan, para que --top N dé las "
                        "N SIGUIENTES)")
    args = p.parse_args()

    jobs = load_jobs()

    if args.urls_from:
        # Modo lista externa: las URLs mandan (vienen de un pool ya filtrado
        # con sus propios criterios, p.ej. el junior o los pools por rol).
        with open(args.urls_from, encoding="utf-8") as f:
            wanted = [r["url"] for r in csv.DictReader(f) if r.get("url")]
        by_url = {j.url: j for j in jobs}
        sel = [by_url[u] for u in wanted if u in by_url]
        missing = len(wanted) - len(sel)
        if missing:
            log(f"{missing} URLs del CSV no están en el último run — saltadas")
        if os.path.exists(TRACKER) and not args.regen:
            with open(TRACKER, encoding="utf-8") as f:
                tracked = {r["url"] for r in csv.DictReader(f)}
            sel = [j for j in sel if j.url not in tracked]
        # Con lista externa el CSV manda: --top solo recorta si se pasa
        # explícitamente (el default 10 truncaba pools enteros en silencio).
        if "--top" in sys.argv:
            sel = sel[:args.top]
        run_selection(sel, args)
        return

    verdicts = {"APTA", "REVISAR"} if args.include_revisar else {"APTA"}
    # El umbral de stack también aplica aquí: filtrar solo por verdict colaba
    # cosas como "Senior Software Engineer, Mobile" con stack 1/10, que es
    # REVISAR por geografía pero no tiene nada que ver con el perfil.
    sel = [j for j in jobs if j.verdict in verdicts
           and _CLF.meets_stack_floor(j)]
    if args.only_ai:
        sel = [j for j in sel if j.is_ai_role]
    if args.company:
        sel = [j for j in sel if args.company.lower() in j.company.lower()]
    if args.location:
        sel = [j for j in sel if args.location.lower() in j.location.lower()]
    # Saltar lo ya tramitado: sin esto, --top 15 devolvía siempre las mismas
    # 15 y "seguir aplicando" obligaba a regenerar dossiers ya enviados.
    #   por defecto: fuera todo lo que está en el tracker (--top N = las N SIGUIENTES)
    #   --regen:     regenera las 'pendiente' (p.ej. para rehacer cartas),
    #                pero las 'enviada' no se tocan JAMÁS.
    if os.path.exists(TRACKER):
        with open(TRACKER, encoding="utf-8") as f:
            rows_t = list(csv.DictReader(f))
        sent = {r["url"] for r in rows_t if r.get("estado") == "enviada"}
        tracked = {r["url"] for r in rows_t}
        skip = sent if args.regen else tracked
        before = len(sel)
        sel = [j for j in sel if j.url not in skip]
        if before != len(sel):
            what = "enviadas" if args.regen else "ya en el tracker"
            log(f"{before - len(sel)} {what} — saltadas")
    # Roles AI primero; dentro, los que piden 2-4 años; luego encaje de stack.
    sel.sort(key=lambda j: (
        not j.is_ai_role,
        0 if _CLF.in_ideal_years(j) else 1,
        -j.stack_score, -(j.sort_salary or 0)))
    sel = sel[:args.top]
    run_selection(sel, args)


def run_selection(sel: list[Job], args) -> None:
    """Genera los dossiers de una selección ya decidida (con pre-vuelo LLM)."""
    if not sel:
        sys.exit("Ninguna oferta cumple el filtro.")

    profile = _read_profile()
    # `key` puede quedar vacío teniendo Gemini: _post_with_backoff enruta solo.
    use_llm = not args.no_ai and ai_filter.has_llm()
    key = ai_filter._resolve_key() if use_llm else ""
    if not args.no_ai and not use_llm:
        log("Sin key de Groq ni Gemini: genero los dossiers sin carta.")

    os.makedirs(APPS_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = []
    for i, job in enumerate(sel, 1):
        log(f"[{i}/{len(sel)}] {job.company} — {job.title[:50]}")
        # Pre-vuelo: el verificador LLM lee el anuncio ENTERO antes de gastar
        # dossier (y tu tiempo). De 41 pendientes de la primera tanda, 34 eran
        # US-only, senior 5+ o híbridas — el clasificador de keywords no llega.
        if use_llm:
            try:
                import verify_kit
                verdict, reason = verify_kit.verify_one(job)
                if verdict == "KILL":
                    log(f"  ✗ verificador: {reason}")
                    rows.append({
                        "estado": "descartada", "empresa": job.company,
                        "puesto": job.title, "ubicacion": job.location,
                        "years": job.years_required or "",
                        "ai_role": "sí" if job.is_ai_role else "no",
                        "stack": job.stack_score, "salario": job.salary_text or "",
                        "url": job.url, "carpeta": "", "generado": now,
                        "aplicado": "",
                    })
                    continue
                if verdict == "UNCLEAR":
                    log(f"  ? verificador dudoso: {reason} — sigo, revísala tú")
            except Exception as exc:  # noqa: BLE001 — el verificador nunca bloquea
                log(f"  · verificador falló ({type(exc).__name__}); sigo")
        questions = fetch_form_questions(job)
        carta = ""
        open_answers: dict[str, str] = {}
        if use_llm:
            try:
                carta = generate_cover_letter(key, profile, job)
            except Exception as exc:  # noqa: BLE001
                log(f"  carta falló: {exc}")
            open_answers = answer_open_questions(key, profile, job, questions)
        folder = write_dossier(job, questions, carta, open_answers)
        log(f"  → {folder}"
            f" ({len(questions)} preguntas, carta: {'sí' if carta else 'no'})")
        rows.append({
            "estado": "pendiente", "empresa": job.company, "puesto": job.title,
            "ubicacion": job.location, "years": job.years_required or "",
            "ai_role": "sí" if job.is_ai_role else "no",
            "stack": job.stack_score, "salario": job.salary_text or "",
            "url": job.url, "carpeta": folder,
            "generado": now, "aplicado": "",
        })

    update_tracker(rows)
    print(f"\n  ✅ {len(rows)} dossiers en {APPS_DIR}/")
    print(f"  📋 seguimiento: {TRACKER}")
    print("\n  Siguiente paso: abre cada carpeta, revisa carta.md, y pega en el"
          "\n  formulario del enlace de oferta.md. El submit es manual: ningún"
          "\n  ATS expone envío público para candidatos.")


if __name__ == "__main__":
    main()
