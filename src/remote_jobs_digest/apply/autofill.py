#!/usr/bin/env python3
"""Auto-relleno de formularios de candidatura — por defecto se detiene ANTES
de enviar.

Abre el formulario en un navegador visible, rellena todo lo que sabe (datos
personales, CV adjunto, carta, preguntas de screening) y **para**, dejando el
botón de enviar a la vista para que lo revises y lo pulses tú.

Por qué no envía solo por defecto: una solicitud enviada no se puede retirar.
Un error en la carta o en una respuesta de screening se replicaría en todas
las ofertas antes de que nadie lo viera, y quemaría cada empresa afectada. El
coste de revisar es de segundos; el de equivocarse, permanente. Por eso el
auto-envío real (--auto-send, ver más abajo) es opt-in con doble
confirmación, nunca el default.

Uso:
    python autofill.py --all                  # todas las pendientes del tracker
    python autofill.py --company Twilio       # una empresa
    python autofill.py --folder applications/scale-ai__software-engineer
    python autofill.py --all --dry-run        # solo dice qué rellenaría
    RJS_ALLOW_AUTO_SEND=1 python autofill.py --all --real-browser \\
        --auto-send --auto-send-limit 1       # envía de verdad (irreversible)

Requiere:  pip install playwright && playwright install chromium
"""

from __future__ import annotations

from remote_jobs_digest import paths

import argparse
import csv
import fcntl
import os
import random
import re
import select
import sys
import termios
import threading
import time
import tty
from datetime import date, datetime, timezone

from urllib.parse import parse_qs, urlsplit

from remote_jobs_digest import ai_filter
from remote_jobs_digest.apply import answer_kit
from remote_jobs_digest.apply.identity import Identity, IdentityError, field_map, load_identity

APPS_DIR = str(paths.applications_dir())
TRACKER = os.path.join(APPS_DIR, "tracker.csv")
AUTO_SEND_LOG = os.path.join(APPS_DIR, "auto_send_log.csv")
AUTO_SEND_LOG_COLS = ["timestamp_utc", "empresa", "puesto", "url", "ats_kind",
                      "captcha_pre", "captcha_post", "resultado", "detalle"]
CV_PATH = os.path.expanduser(os.getenv("RJS_CV_PATH", "~/cv.pdf"))
# Perfil propio para --real-browser. Deliberadamente NO es ~/.config/google-chrome:
# usar el perfil principal falla si tienes Chrome abierto y arriesga corromperlo.
CHROME_PROFILE = os.path.expanduser(
    os.getenv("RJS_CHROME_PROFILE", "~/.config/rjs-chrome-profile"))

# Los campos de identidad (nombre, email, teléfono...) ya no viven aquí como
# literales de módulo: identity.py::field_map(identity) los genera a partir
# de un Identity real, cargado una vez en main() con load_identity() (Fase 3
# de docs/OSS-PLAN.md — resuelve el hallazgo D4 de
# design/CODEBASE-DESIGN.md). fill_one() recibe `identity` como parámetro.

# Campos de texto largo que conviene rellenar si existen.
TEXTAREA_MAP: list[tuple[str, str, str]] = [
    ("Info adicional", r"additional information|anything else|cover letter",
     ""),   # se rellena con la carta; ver fill_one
]

# Preguntas de opción única resueltas con radio buttons (Ashby las usa mucho).
RADIO_CHOICES: list[tuple[list[str], list[str]]] = [
    # (keywords de la pregunta, textos de opción aceptables por orden)
    (["how did you hear", "how did you find", "where did you hear"],
     ["careers page", "career site", "company website", "job board", "other"]),
    (["require sponsorship", "need sponsorship", "visa sponsorship"], ["no"]),
    (["legally authorized", "authorized to work", "eu citizen",
      "right to work"], ["yes"]),
]

# Desplegables. Greenhouse no usa <select> nativos sino react-select
# (role="combobox"), donde .fill() no hace nada: hay que hacer clic, escribir y
# pulsar Enter. El valor es la opción a buscar; basta con que sea un prefijo
# único ("Citizen or permanent" casa con la opción larga completa).
SELECT_CHOICES: list[tuple[list[str], str]] = [
    (["require sponsorship", "need sponsorship", "sponsorship for employment",
      "require visa"], "No"),
    (["eu citizen", "european union member state"], "Yes"),
    (["legally authorized", "authorized to work"], "Yes"),
    (["source of your right", "basis of your right"],
     "Citizen or permanent resident"),
    (["previously been employed", "former employee"], "have not"),
    (["how did you hear", "how did you first learn", "how did you find"],
     "Career"),
    (["acknowledge", "i confirm i have read", "privacy policy"], "Acknowledge"),
    (["pronouns"], "He"),
    (["country"], "Spain"),
    # Cosecha de la sonda 2026-08-04 (huecos reales vistos en formularios).
    # TEMPORAL: la ciudad real del usuario original se ha sustituido por vacío
    # — ver nota de FIELD_MAP más arriba, mismo motivo (no PII en el repo OSS).
    (["location (city", "location city", "current city"], ""),
    (["time zone", "timezone"], "Central European"),
    (["legally authorised", "work authorisation", "necessary work "
      "authorization"], "Yes"),
    (["visa sponsorship"], "No"),
    (["ever been employed by", "previously worked for"], "No"),
    (["fluency in english", "english level", "minimum c1"], "Yes"),
    # Sin mudanza: es el criterio de toda la búsqueda (100% remoto).
    (["willing to relocate"], "No"),
]
# Nota deliberada: género / hispanic-latino / veteran / disability NO se
# rellenan nunca — son preguntas EEO protegidas y las contesta el candidato.

CV_SELECTORS = ["input[type='file'][name*='resume' i]",
                "input[type='file'][id*='resume' i]",
                "input[type='file']"]

COVER_SELECTORS = ["textarea[name*='cover' i]", "textarea[id*='cover' i]",
                   "#cover_letter_text", "textarea[name='comments']"]


def log(msg: str) -> None:
    print(msg, flush=True)


# --- Lectura de los dossiers -------------------------------------------------

def read_pending(args) -> list[dict]:
    if args.folder:
        folder = os.path.abspath(args.folder)
        return [{"carpeta": folder, "empresa": os.path.basename(folder),
                 "puesto": "", "url": _url_from_dossier(folder),
                 "estado": "pendiente"}]
    if not os.path.exists(TRACKER):
        sys.exit(f"No hay tracker en {TRACKER}. Corre antes: python apply_kit.py")
    with open(TRACKER, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows
            if r.get("estado", "") not in ("enviada", "descartada")]
    if args.company:
        rows = [r for r in rows if args.company.lower() in r["empresa"].lower()]
    # Partición para correr 2 ventanas en paralelo sin que se pisen: cada
    # candidatura cae en un lado exacto (kind() es una función pura sobre la
    # URL), así que los dos conjuntos nunca se solapan.
    if args.only_auto_send_ats:
        rows = [r for r in rows
                if _ats_kind_from_url(r.get("url", "")) in AUTO_SEND_ALLOWED_ATS]
    elif args.skip_auto_send_ats:
        rows = [r for r in rows
                if _ats_kind_from_url(r.get("url", "")) not in AUTO_SEND_ALLOWED_ATS]
    return rows


_URL_RE = re.compile(r"\*\*Aplicar en\*\*\s*\|\s*(\S+)")


def _url_from_dossier(folder: str) -> str:
    path = os.path.join(folder, "oferta.md")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        m = _URL_RE.search(f.read())
    return m.group(1) if m else ""


def _locked_tracker_rewrite(update_fn) -> None:
    """Lee TRACKER, aplica `update_fn(rows, cols)` (muta `rows` in place) y
    reescribe — todo bajo un lock exclusivo (`fcntl.flock`) sobre un fichero
    `.lock` hermano. Sin esto, dos procesos escribiendo el mismo tracker.csv
    casi a la vez (p.ej. una ventana de auto-envío y otra de relleno manual
    corriendo en paralelo) pueden perder el cambio del otro: ambos leen el
    mismo estado, cada uno escribe su versión, y el que escribe segundo pisa
    al primero sin fundir los cambios. El lock serializa el read-modify-write
    completo entre procesos."""
    if not os.path.exists(TRACKER):
        return
    lock_path = TRACKER + ".lock"
    with open(lock_path, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            with open(TRACKER, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                cols, rows = reader.fieldnames or [], list(reader)
            update_fn(rows, cols)
            with open(TRACKER, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def mark_state(row: dict, estado: str) -> None:
    """Actualiza el estado de UNA candidatura bajo lock (seguro frente a otro
    proceso —p.ej. una segunda ventana en paralelo— escribiendo el tracker a
    la vez, no solo frente a que lo toque "entre medias")."""
    def _update(rows: list[dict], cols: list[str]) -> None:
        for r in rows:
            if r.get("url") == row.get("url"):
                r["estado"] = estado
                if estado in ("enviada", "enviada_auto"):
                    r["aplicado"] = date.today().isoformat()
    _locked_tracker_rewrite(_update)


def mark_sent(row: dict) -> None:
    mark_state(row, "enviada")


def read_cover_letter(folder: str) -> str:
    path = os.path.join(folder, "carta.md")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    # El fichero lleva un título y un aviso en blockquote: nos quedamos el cuerpo.
    body = [l for l in lines if not l.startswith("#") and not l.startswith(">")]
    return "\n".join(body).strip()


def read_form_answers(folder: str) -> list[tuple[str, str]]:
    """(pregunta, respuesta) de formulario.md, saltando las pendientes."""
    path = os.path.join(folder, "formulario.md")
    if not os.path.exists(path):
        return []
    out, label = [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("## "):
                label = re.sub(r"\*\*|\(obligatoria\)|\(opcional\)", "",
                               line[3:]).strip()
            elif line.startswith("**Respuesta:**") and label:
                ans = line[len("**Respuesta:**"):].strip()
                if ans and "_(pendiente)_" not in ans:
                    out.append((label, ans))
                label = None
    return out


# --- Salto en caliente ("no me interesa, siguiente") -------------------------
#
# fill_one tarda varios segundos en recorrer ~30 campos + CV + carta +
# preguntas de screening. El nombre de empresa/puesto ya sale en el log antes
# de que empiece a rellenar, y a menudo el propio formulario (seniority,
# "US only", agencia de contratación...) lo confirma a los pocos campos.
# Esperar a que fill_one termine para poder pulsar 'd' en el prompt final
# desperdicia ese tiempo en cada oferta que se va a descartar igualmente.

class SkipRequested(Exception):
    """El usuario pidió saltar esta oferta a media ejecución de fill_one."""


class _SkipListener:
    """Escucha una tecla en la terminal (sin esperar Enter) mientras se
    rellena, para poder cortar sin esperar a que fill_one termine.

    Usa termios/tty en vez de una lib de teclado global: no requiere permisos
    especiales y basta con que la terminal tenga el foco, que es justo donde
    se ven los logs de cada campo rellenándose. Si stdin no es una terminal
    real (p.ej. corriendo en cron) se desactiva sola en vez de fallar.
    """

    KEY = "d"

    def __init__(self) -> None:
        self.event = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._old_settings = None
        self._fd = None

    def __enter__(self) -> "_SkipListener":
        if sys.stdin.isatty():
            self._fd = sys.stdin.fileno()
            try:
                self._old_settings = termios.tcgetattr(self._fd)
                tty.setcbreak(self._fd)
                self._thread = threading.Thread(target=self._listen, daemon=True)
                self._thread.start()
            except Exception:  # noqa: BLE001
                self._old_settings = None
        return self

    def _listen(self) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
            if ready and sys.stdin.read(1).lower() == self.KEY:
                self.event.set()
                return

    def check(self) -> None:
        if self.event.is_set():
            raise SkipRequested()

    def __exit__(self, *exc_info) -> bool:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
        if self._old_settings is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_settings)
        return False


def _wait_checkable(page, ms: int, skip: "_SkipListener | None") -> None:
    """page.wait_for_timeout troceado en pasos de 200ms para poder cortar a
    media espera, no solo entre campos."""
    step = 200
    elapsed = 0
    while elapsed < ms:
        if skip:
            skip.check()
        chunk = min(step, ms - elapsed)
        page.wait_for_timeout(chunk)
        elapsed += chunk


# --- Relleno -----------------------------------------------------------------

def fill_one(page, row: dict, dry_run: bool,
            skip: "_SkipListener | None" = None,
            identity: Identity | None = None) -> int:
    identity = identity or Identity.empty()
    folder = row["carpeta"]
    if not os.path.isabs(folder):
        folder = os.path.join(str(paths.data_dir()), folder)
    url = row.get("url") or _url_from_dossier(folder)
    if not url:
        log("  ✗ sin URL de aplicación")
        return 0

    log(f"\n  → {url}")
    if dry_run:
        log(f"    [dry-run] rellenaría {len(field_map(identity))} campos"
            f" + CV + carta ({len(read_cover_letter(folder))} chars)"
            f" + {len(read_form_answers(folder))} respuestas de screening")
        return 0

    if skip:
        skip.check()
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    _wait_checkable(page, 2500, skip)

    # Ashby (y a veces Greenhouse) esconden el formulario tras un botón.
    # La espera es de 3 s: con 1,5 s el formulario aún no estaba montado y el
    # relleno acababa en "0 campos". El wait_for_timeout vive FUERA del
    # try/except: si estuviera dentro, un SkipRequested lanzado a media
    # espera lo tragaría el "except Exception: pass" en vez de propagarse.
    for label in ("Apply for this job", "Apply now", "Apply for this position",
                  "Apply"):
        if skip:
            skip.check()
        clicked = False
        try:
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            if btn.count() and btn.first.is_visible():
                btn.first.click()
                clicked = True
        except Exception:  # noqa: BLE001
            pass
        if clicked:
            _wait_checkable(page, 3000, skip)
            break

    # Personio: el botón de arriba solo prueba texto en inglés, y el anuncio
    # de Personio a menudo está en el idioma de la empresa (p.ej. "Solicitar
    # este puesto de trabajo") — el clic genérico no lo encuentra. Además el
    # formulario NUNCA vive en la página del anuncio: vive en /job/{id}/apply.
    # Si tras el intento genérico no hay ni un input ni un textarea visible,
    # se navega ahí directamente (confirmado en vivo, 2026-08-04).
    if _ats_kind_from_url(url) == "personio" and not url.rstrip("/").endswith("/apply"):
        try:
            if not page.locator("input, textarea").count():
                page.goto(url.rstrip("/") + "/apply",
                          wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(2000)
        except Exception:  # noqa: BLE001
            pass

    # Algunos ATS llevan a /application en vez de desplegar en la misma página.
    try:
        page.wait_for_selector("input, textarea", timeout=8000)
    except Exception:  # noqa: BLE001
        pass

    filled = 0
    for name, label_rx, value, selectors in field_map(identity):
        if skip:
            skip.check()
        if fill_by_label(page, label_rx, value) or \
                fill_by_css(page, selectors, value):
            log(f"    ✓ {name}")
            filled += 1

    if skip:
        skip.check()
    # CV
    if os.path.exists(CV_PATH):
        for sel in CV_SELECTORS:
            try:
                el = page.locator(sel).first
                if el.count():
                    el.set_input_files(CV_PATH)
                    log(f"    ✓ CV adjuntado ({os.path.basename(CV_PATH)})")
                    filled += 1
                    break
            except Exception:  # noqa: BLE001
                continue
    else:
        log(f"    ⚠ no encuentro el CV en {CV_PATH} (usa RJS_CV_PATH)")

    if skip:
        skip.check()
    # Carta
    carta = read_cover_letter(folder)
    if carta:
        for sel in COVER_SELECTORS:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    el.fill(carta)
                    log("    ✓ carta de presentación")
                    filled += 1
                    break
            except Exception:  # noqa: BLE001
                continue

    # Preguntas de texto libre: se buscan por aria-label, que en Greenhouse es
    # la única etiqueta legible de los campos question_*.
    skip_labels = (
        "first name", "last name", "full name", "email", "phone", "resume",
        "cover letter", "linkedin", "website", "github", "portfolio",
        "preferred name", "current company", "current title",
        "current location", "country", "pronouns")
    for label, answer in read_form_answers(folder):
        if skip:
            skip.check()
        low = _norm(label)
        if low.startswith(skip_labels):
            continue                      # ya cubiertos arriba
        if _choice_for(low):
            continue                      # es un desplegable: más abajo
        for sel in (f"input[aria-label*='{label[:34]}' i]",
                    f"textarea[aria-label*='{label[:34]}' i]"):
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    el.fill(answer)
                    log(f"    ✓ {label[:52]}")
                    filled += 1
                    break
            except Exception:  # noqa: BLE001
                continue

    filled += fill_selects(page, skip=skip)
    filled += fill_radios(page, skip=skip)
    dump_open_questions(page, folder, row)

    log(f"    {filled} campos rellenados.")
    log("    ⚠️  REVISA la página antes de enviar: los desplegables se "
        "rellenan a ciegas y algún consentimiento puede quedar suelto.")
    return filled


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).lower().strip()


# Etiquetas que ya cubre el auto-relleno: no son preguntas que redactar.
_KNOWN_LABELS = re.compile(
    r"first name|last name|full name|^name$|e-?mail|phone|resume|cv|"
    r"cover letter|linkedin|github|portfolio|website|twitter|current "
    r"(company|title|location)|country|pronouns|preferred name|city|"
    r"how did you hear|sponsorship|authorized|citizen|salary|notice|other",
    re.I)


def dump_open_questions(page, folder: str, row: dict | None = None) -> int:
    """Cierra el hueco entre "detectar" y "responder": las preguntas que solo
    viven en el DOM (Ashby y Lever no las publican por API, a diferencia de
    Greenhouse) se leen del formulario ya cargado y, si son narrativas
    (`answer_kit.is_open_question`) y hay LLM disponible, se responden con
    `answer_kit.answer_question()` y se rellenan ahí mismo. Las protegidas
    (EEO/demográficas, vía `answer_kit.DO_NOT_ANSWER`) o las que fallan por
    cualquier motivo se vuelcan a `preguntas-detectadas.md` como antes, para
    `./rjs answer --file <fichero>` a mano."""
    found: list[tuple[object, str]] = []
    try:
        for el in page.locator("textarea").all():
            if not el.is_visible():
                continue
            label = el.evaluate("""e => {
                if (e.id) {
                  const l = document.querySelector('label[for="' + CSS.escape(e.id) + '"]');
                  if (l) return l.innerText;
                }
                const w = e.closest('label');
                if (w) return w.innerText;
                if (e.getAttribute('aria-label')) return e.getAttribute('aria-label');
                const r = e.getAttribute('aria-labelledby');
                if (r) { const n = document.getElementById(r); if (n) return n.innerText; }
                return '';
            }""") or ""
            label = re.sub(r"\s+", " ", label).strip()
            if len(label) < 20 or _KNOWN_LABELS.search(label):
                continue
            found.append((el, label))
    except Exception:  # noqa: BLE001
        return 0
    if not found:
        return 0

    row = row or {}
    answered: list[tuple[str, str]] = []
    unanswered: list[str] = []
    use_llm = ai_filter.has_llm()
    key = ai_filter._resolve_key() if use_llm else ""
    profile = answer_kit.read_profile() if use_llm else ""
    for el, label in found:
        if not use_llm or not answer_kit.is_open_question(label):
            unanswered.append(label)
            continue
        try:
            ans = answer_kit.answer_question(key, profile, label,
                                             company=row.get("empresa", ""))
        except Exception:  # noqa: BLE001 — un fallo del LLM no bloquea el resto
            ans = ""
        if not ans:
            unanswered.append(label)
            continue
        try:
            el.fill(ans)
            log(f"    ✓ [auto-LLM] {label[:50]}")
            answered.append((label, ans))
        except Exception:  # noqa: BLE001
            unanswered.append(label)

    if answered:
        path = os.path.join(folder, "preguntas-respondidas.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Preguntas abiertas respondidas automáticamente (LLM)\n\n")
            f.write("> Ya están escritas en el formulario. REVÍSALAS antes de "
                    "enviar — no se ha comprobado una a una.\n\n")
            for label, ans in answered:
                f.write(f"## {label}\n\n{ans}\n\n")
        log(f"    ✎ {len(answered)} pregunta(s) respondidas automáticamente "
            f"→ preguntas-respondidas.md")

    if unanswered:
        path = os.path.join(folder, "preguntas-detectadas.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Preguntas abiertas detectadas en el formulario\n\n")
            f.write("> Extraídas del DOM (Ashby y Lever no las publican por API). "
                    "Protegidas (EEO) o el LLM no estaba disponible/falló.\n")
            f.write(f"> Redactarlas:  ./rjs answer --file {path}\n\n")
            for q in unanswered:
                f.write(f"{q}\n")
        log(f"    ✎ {len(unanswered)} pregunta(s) sin responder → "
            f"preguntas-detectadas.md")
    return len(found)


def fill_by_label(page, label_rx: str, value: str) -> bool:
    """Rellena el primer campo visible cuya etiqueta case con el patrón.

    `get_by_label` resuelve `<label for>`, `aria-label` y `aria-labelledby`, así
    que sirve para Greenhouse y Ashby por igual — que etiquetan de formas
    distintas.
    """
    if not value:
        return False
    try:
        loc = page.get_by_label(re.compile(label_rx, re.I))
        for i in range(min(loc.count(), 4)):
            el = loc.nth(i)
            if not el.is_visible():
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            if tag not in ("input", "textarea"):
                continue
            if el.evaluate("e => e.type") in ("file", "radio", "checkbox"):
                continue
            el.fill(value)
            return True
    except Exception:  # noqa: BLE001 — campo ausente o no rellenable
        pass
    return False


def fill_by_css(page, selectors: list[str], value: str) -> bool:
    """Red de seguridad para campos sin etiqueta accesible."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible():
                el.fill(value)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def fill_radios(page, skip: "_SkipListener | None" = None) -> int:
    """Preguntas de opción única con radio buttons (habitual en Ashby)."""
    done = 0
    for keywords, options in RADIO_CHOICES:
        if skip:
            skip.check()
        try:
            for opt in options:
                radio = page.get_by_role("radio", name=re.compile(
                    rf"^\s*{re.escape(opt)}", re.I))
                if not radio.count() or not radio.first.is_visible():
                    continue
                # Solo si la pregunta de alrededor es la que buscamos.
                ctx = _norm(radio.first.evaluate(
                    "e => (e.closest('fieldset') || e.closest('div'))"
                    "?.innerText || ''")[:220])
                if not any(k in ctx for k in keywords):
                    continue
                radio.first.check()
                log(f"    ✓ [radio] {keywords[0][:34]} → {opt}")
                done += 1
                break
        except Exception:  # noqa: BLE001
            continue
    return done


def _choice_for(low_label: str) -> str:
    for keywords, choice in SELECT_CHOICES:
        if any(k in low_label for k in keywords):
            return choice
    return ""


def _combo_label(page, combo) -> str:
    """Etiqueta de un react-select de Greenhouse.

    No usan `aria-label`: apuntan con `aria-labelledby` a un elemento
    `#question_XXXXX-label` que contiene el enunciado. Buscar por aria-label
    devolvía siempre vacío, que es por lo que ningún desplegable se rellenaba.
    """
    ref = combo.get_attribute("aria-labelledby")
    if ref:
        try:
            el = page.locator(f"#{ref}").first
            if el.count():
                return _norm(el.inner_text())
        except Exception:  # noqa: BLE001
            pass
    return _norm(combo.get_attribute("aria-label") or "")


def fill_selects(page, skip: "_SkipListener | None" = None) -> int:
    """Rellena los react-select de Greenhouse.

    No son <select> nativos, así que select_option() no sirve: hay que abrir el
    combobox, teclear la opción y confirmar con Enter.
    """
    done = 0
    try:
        combos = page.locator("[role='combobox']").all()
    except Exception:  # noqa: BLE001
        return 0
    for combo in combos:
        if skip:
            skip.check()
        try:
            if (combo.get_attribute("id") or "").startswith("iti-"):
                continue          # buscador de prefijo del widget de teléfono
            label = _combo_label(page, combo)
            choice = _choice_for(label)
            if not choice:
                if label:
                    log(f"    · [select] sin respuesta definida: {label[:52]}")
                continue
            combo.click()
            _wait_checkable(page, 400, skip)
            page.keyboard.type(choice, delay=45)
            _wait_checkable(page, 700, skip)
            page.keyboard.press("Enter")
            _wait_checkable(page, 300, skip)
            log(f"    ✓ [select] {label[:44]} → {choice}")
            done += 1
        except SkipRequested:
            raise  # no lo trague el except genérico de abajo
        except Exception as exc:  # noqa: BLE001 — un desplegable raro no para el resto
            log(f"    · select sin resolver ({type(exc).__name__})")
            continue
    return done


# --- Auto-envío (opt-in, NUNCA por defecto) ---------------------------------
#
# Todo lo de aquí abajo solo se ejecuta con --auto-send + RJS_ALLOW_AUTO_SEND=1
# (doble confirmación deliberada). El resto del script (--dry-run, --probe,
# relleno normal) se comporta exactamente igual que antes: nunca localiza ni
# toca un botón de Enviar salvo que se pida este modo explícitamente.
#
# Greenhouse usa Invisible reCAPTCHA (puntúa movimiento de ratón/tecleo);
# Lever y Workable usan Cloudflare Turnstile — en Workable aparece justo al
# pulsar Enviar, no al cargar el formulario (ver README.md). SmartRecruiters
# usa DataDome (bot-management conductual, adoptado explícitamente contra
# candidaturas automatizadas — ver caso de estudio de DataDome/SmartRecruiters,
# 2026-02): se excluye igual que Lever/Workable, no por precaución genérica
# sino porque hay evidencia pública concreta. Recruitee y Personio no imponen
# captcha por defecto (Recruitee: opt-in por empresa/oferta; Personio: solo si
# la empresa monta su propio career page vía API) — piloto supervisado con
# límite bajo, revisando `auto_send_log.csv` antes de confiar en volumen.
# Aun con ATS permitido se comprueba captcha antes Y después del click.

AUTO_SEND_ALLOWED_ATS = {"greenhouse", "ashby", "recruitee", "personio"}

_ATS_HOST_PATTERNS = [
    ("greenhouse", re.compile(r"greenhouse\.io", re.I)),
    ("ashby", re.compile(r"ashbyhq\.com", re.I)),
    ("lever", re.compile(r"lever\.co", re.I)),
    ("workable", re.compile(r"workable\.com", re.I)),
    ("recruitee", re.compile(r"recruitee\.com", re.I)),
    ("personio", re.compile(r"personio\.(de|com)", re.I)),
    ("smartrecruiters", re.compile(r"smartrecruiters\.com", re.I)),
]

SUBMIT_SELECTORS = {
    "greenhouse": ["button#submit_app", "button:has-text('Submit Application')"],
    "ashby": ["button:has-text('Submit Application')", "button[type='submit']"],
    # data-testid confirmado en vivo (2026-08-04) contra una oferta real de
    # Recruitee: es el selector del propio botón, más fiable que el texto
    # (que puede venir traducido según el idioma del anuncio).
    "recruitee": ["button[data-testid='submit-application-form-button']",
                  "button[type='submit']"],
    # Clase confirmada en vivo contra una oferta real de Personio. El
    # formulario vive en /job/{id}/apply, no en la página del anuncio — ver
    # el fallback de navegación en fill_one().
    "personio": ["button.career-submit-application-btn", "button[type='submit']"],
}

_CAPTCHA_SELECTORS = [
    ("recaptcha", "iframe[src*='recaptcha'], div.g-recaptcha, [data-sitekey]"),
    ("hcaptcha", "iframe[src*='hcaptcha'], div.h-captcha"),
    ("turnstile", "iframe[src*='challenges.cloudflare.com'], div.cf-turnstile"),
]
_CHALLENGE_TEXT_RE = re.compile(
    r"verifying you are human|checking your browser", re.I)
_SUCCESS_URL_RE = re.compile(r"thank|confirm", re.I)
# Ampliado tras el primer intento real en Ashby (2026-08-06, Cosuno): las 3
# frases originales no cubrían variantes habituales de confirmación
# ("Your application has been submitted", "We've received your
# application"...) y el intento quedó AMBIGUO sin poder confirmar si en
# realidad se había enviado. No se sabe con certeza qué texto mostró Ashby
# ese caso concreto — esto reduce el riesgo de que se repita, no lo resuelve
# retroactivamente.
_SUCCESS_TEXT_RE = re.compile(
    r"application (has been |was )?(received|submitted)|"
    r"thank you for (applying|your application)|thanks for applying|"
    r"successfully submitted|we('ve| have) received your application|"
    r"application (complete|confirmed)", re.I)
# Fallo explícito (distinto de "ambiguo"): el ATS SÍ dice que no se envió, no
# es que no hayamos sabido detectarlo. Frases confirmadas en el código de
# github.com/Aaditya231250/NoPe (leído el 2026-08-06).
_FAILURE_TEXT_RE = re.compile(
    r"couldn.t submit your application|could not submit your application|"
    r"flagged as (possible )?spam|application submission (failed|was flagged)",
    re.I)


def _ats_kind_from_url(url: str) -> str:
    for kind, rx in _ATS_HOST_PATTERNS:
        if rx.search(url):
            return kind
    return ""


def detect_captcha(page) -> str:
    """Nombre corto del captcha/challenge visible, o "" si no hay ninguno."""
    for name, sel in _CAPTCHA_SELECTORS:
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                return name
        except Exception:  # noqa: BLE001
            continue
    try:
        if _CHALLENGE_TEXT_RE.search(page.locator("body").inner_text()):
            return "challenge-text"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _find_submit_button(page, ats_kind: str):
    for sel in SUBMIT_SELECTORS.get(ats_kind, []):
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible():
                return el
        except Exception:  # noqa: BLE001
            continue
    return None


def _is_submission_response(ats_kind: str, url: str) -> bool:
    """Señal de red: el POST real que crea la candidatura, no una mutación de
    autoguardado de un campo cualquiera. Patrón verificado leyendo el código
    fuente de github.com/Aaditya231250/NoPe (2026-08-06) — mucho más fiable
    que adivinar texto/URL, que es justo lo que dio un resultado AMBIGUO en
    el primer intento real (Cosuno, Ashby, 2026-08-06): Ashby dispara
    mutaciones GraphQL por cada campo mientras rellenas, así que cualquier
    respuesta a esa misma ruta no significa que se creó la candidatura —
    solo cuenta la mutación final exacta o el endpoint legacy dedicado."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/").lower()
    if ats_kind == "ashby" and host == "jobs.ashbyhq.com":
        if path == "/api/non-user-application-form":
            return True
        ops = {v.lower() for v in parse_qs(parsed.query).get("op", [])}
        return path == "/api/non-user-graphql" and \
            "apisubmitsingleapplicationformaction" in ops
    if ats_kind == "greenhouse" and "greenhouse.io" in host:
        return "/jobs/" in path
    return False


def _wait_submit_result(page, ats_kind: str, post_responses: list,
                        timeout_s: float = 40.0) -> str:
    """"exito" / "fallo_explicito" / "ambiguo". Combina la señal de red
    (primaria en Ashby/Greenhouse) con el texto visible (única señal para
    Recruitee/Personio, que no tienen endpoint verificado — degrada
    limpiamente a lo que ya había antes). El timeout largo no es capricho:
    Ashby puede tardar hasta 30s en resolver su Invisible reCAPTCHA antes de
    responder de verdad; con el timeout corto anterior (10s) el primer
    intento real dio "ambiguo" quizá solo por cortar la espera demasiado
    pronto, no porque el envío fallara."""
    deadline = time.monotonic() + timeout_s
    net_matched_at: float | None = None
    while time.monotonic() < deadline:
        try:
            text = page.locator("body").inner_text()
        except Exception:  # noqa: BLE001
            text = ""
        if _FAILURE_TEXT_RE.search(text):
            return "fallo_explicito"
        if _SUCCESS_URL_RE.search(page.url) or _SUCCESS_TEXT_RE.search(text):
            return "exito"
        if any(_is_submission_response(ats_kind, r.url) for r in post_responses):
            if net_matched_at is None:
                net_matched_at = time.monotonic()
            elif time.monotonic() - net_matched_at >= 2.0:
                return "exito"
        page.wait_for_timeout(500)
    return "ambiguo"


def _log_auto_send(row: dict, ats_kind: str, captcha_pre: str, captcha_post: str,
                    resultado: str, detalle: str) -> None:
    """Traza forense de cada intento de auto-envío (éxito o no), separada del
    tracker: aquí queda constancia incluso de lo abortado."""
    os.makedirs(APPS_DIR, exist_ok=True)
    is_new = not os.path.exists(AUTO_SEND_LOG)
    with open(AUTO_SEND_LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=AUTO_SEND_LOG_COLS)
        if is_new:
            w.writeheader()
        w.writerow({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "empresa": row.get("empresa", ""), "puesto": row.get("puesto", ""),
            "url": row.get("url", ""), "ats_kind": ats_kind,
            "captcha_pre": captcha_pre, "captcha_post": captcha_post,
            "resultado": resultado, "detalle": detalle,
        })


def try_auto_send(page, row: dict, dry_run_submit: bool = False) -> str:
    """Intenta el envío real de UNA candidatura ya rellenada por fill_one().

    Devuelve "enviada" (confirmado), "abortada" (no se intentó o se frenó
    ANTES del click — seguro reintentar a mano, el estado no cambia),
    "ambigua" (posible click sin confirmación clara — nunca se asume éxito;
    quien llama debe parar el resto del batch de auto-envío) o "simulado"
    (dry_run_submit=True: localizó todo pero no hizo clic)."""
    url = row.get("url", "")
    ats_kind = _ats_kind_from_url(url)
    if ats_kind not in AUTO_SEND_ALLOWED_ATS:
        reason = {"lever": "Turnstile conocido", "workable": "Turnstile conocido",
                  "smartrecruiters": "DataDome conocido"}.get(
            ats_kind, "no evaluado")
        log(f"    ⚠ auto-envío no habilitado para "
            f"{ats_kind or 'este ATS'} ({reason}) — repaso manual")
        _log_auto_send(row, ats_kind, "", "", "abortada", "ats_no_permitido")
        return "abortada"

    captcha_pre = detect_captcha(page)
    if captcha_pre:
        log(f"    ⚠ {captcha_pre} detectado antes de enviar — repaso manual")
        _log_auto_send(row, ats_kind, captcha_pre, "", "abortada", "captcha_pre")
        return "abortada"

    btn = _find_submit_button(page, ats_kind)
    if btn is None:
        log("    ⚠ botón Enviar no localizado — repaso manual")
        _log_auto_send(row, ats_kind, captcha_pre, "", "abortada",
                        "boton_no_encontrado")
        return "abortada"

    if dry_run_submit:
        log("    [dry-run-submit] ATS permitido, sin captcha, botón "
            "localizado — NO hago clic (simulación)")
        _log_auto_send(row, ats_kind, captcha_pre, "", "simulado",
                        "dry_run_submit")
        return "simulado"

    post_responses: list = []

    def _on_response(resp) -> None:
        try:
            if resp.request.method == "POST":
                post_responses.append(resp)
        except Exception:  # noqa: BLE001
            pass

    page.on("response", _on_response)
    try:
        btn.click()
        page.wait_for_timeout(1500)
        captcha_post = detect_captcha(page)
        if captcha_post:
            log(f"    ⚠ {captcha_post} apareció justo al pulsar Enviar — estado "
                f"AMBIGUO, no asumo éxito")
            mark_state(row, "revisar_envio")
            _log_auto_send(row, ats_kind, captcha_pre, captcha_post, "ambigua",
                            "captcha_tras_click")
            return "ambigua"

        result = _wait_submit_result(page, ats_kind, post_responses)
    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:  # noqa: BLE001
            pass

    if result == "exito":
        log("    ✅ envío confirmado")
        mark_state(row, "enviada_auto")
        _log_auto_send(row, ats_kind, captcha_pre, captcha_post,
                        "enviada_auto", "")
        return "enviada"

    if result == "fallo_explicito":
        log("    ✗ el ATS dice explícitamente que NO se envió (no es "
            "ambigüedad nuestra) — repaso manual")
        mark_state(row, "revisar_envio")
        _log_auto_send(row, ats_kind, captcha_pre, captcha_post, "ambigua",
                        "fallo_explicito_ats")
        return "ambigua"

    log("    ⚠ no se confirmó el envío en 40s — estado AMBIGUO, no asumo éxito")
    mark_state(row, "revisar_envio")
    _log_auto_send(row, ats_kind, captcha_pre, captcha_post, "ambigua",
                    "sin_confirmacion")
    return "ambigua"


def main() -> None:
    p = argparse.ArgumentParser(
        description="Rellena formularios de candidatura sin enviarlos")
    p.add_argument("--all", action="store_true", help="todas las pendientes")
    p.add_argument("--company", help="filtrar por empresa")
    p.add_argument("--folder", help="una carpeta de applications/")
    split = p.add_mutually_exclusive_group()
    split.add_argument("--only-auto-send-ats", action="store_true",
                       help="solo candidaturas en ATS elegibles para "
                            "--auto-send (Greenhouse/Ashby/Recruitee/"
                            "Personio) — para correr en una ventana separada "
                            "de --skip-auto-send-ats sin pisaros el tracker")
    split.add_argument("--skip-auto-send-ats", action="store_true",
                       help="solo candidaturas FUERA de los ATS elegibles "
                            "para --auto-send (Lever/Workable/SmartRecruiters"
                            "/otros) — el complementario de "
                            "--only-auto-send-ats")
    p.add_argument("--dry-run", action="store_true",
                   help="no abre navegador: solo dice qué haría")
    p.add_argument("--real-browser", action="store_true",
                   help="usar Google Chrome con perfil persistente "
                        "(necesario en sitios tras Cloudflare, p.ej. Workable)")
    p.add_argument("--open-only", action="store_true",
                   help="solo abre la oferta, sin rellenar nada")
    p.add_argument("--probe", action="store_true",
                   help="MODO SONDA: headless, rellena y cosecha las preguntas "
                        "sin respuesta de cada formulario. NUNCA envía nada — "
                        "este modo no pulsa Enviar.")
    p.add_argument("--auto-send", action="store_true",
                   help="[IRREVERSIBLE] tras rellenar, intenta pulsar Enviar "
                        "de verdad — solo en Greenhouse/Ashby y solo si no hay "
                        "captcha visible. NUNCA es el comportamiento por "
                        "defecto: exige --real-browser y RJS_ALLOW_AUTO_SEND=1.")
    p.add_argument("--auto-send-limit", type=int, default=3,
                   help="máx. candidaturas auto-enviadas en esta ejecución "
                        "(default: 3)")
    p.add_argument("--auto-send-delay", type=float, default=60.0,
                   help="segundos de espera entre auto-envíos, con jitter "
                        "±20%% (default: 60)")
    p.add_argument("--dry-run-submit", action="store_true",
                   help="con --auto-send: hace toda la detección (ATS, "
                        "captcha, botón de Enviar) pero NUNCA hace clic. "
                        "Para validar el modo sin arriesgar un envío real.")
    args = p.parse_args()
    if not (args.all or args.company or args.folder):
        p.error("elige --all, --company o --folder")
    if args.dry_run_submit and not args.auto_send:
        p.error("--dry-run-submit solo tiene sentido junto a --auto-send")
    if args.auto_send:
        if not args.real_browser:
            p.error("--auto-send exige --real-browser (mismo perfil con "
                     "reputación acumulada; un perfil nuevo sin historial "
                     "dispara antibot antes)")
        if os.getenv("RJS_ALLOW_AUTO_SEND") != "1":
            p.error("--auto-send exige también RJS_ALLOW_AUTO_SEND=1 en el "
                     "entorno — doble confirmación deliberada: esto envía "
                     "candidaturas reales sin que las revises tú")

    rows = read_pending(args)
    if not rows:
        sys.exit("No hay candidaturas pendientes en el tracker.")
    log(f"{len(rows)} candidatura(s) pendientes.")

    try:
        identity = load_identity()
    except IdentityError as exc:
        sys.exit(f"identity.local.yaml existe pero no se pudo leer: {exc}")
    if identity == Identity.empty():
        log("(sin identity.local.yaml — los campos de identidad se dejarán "
            "en blanco. Corre './rjs init' o crea el fichero a mano.)")

    if args.dry_run:
        for row in rows:
            log(f"\n[{row['empresa']}] {row.get('puesto','')}")
            fill_one(None, row, dry_run=True, identity=identity)
        return

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Falta playwright: pip install playwright && "
                 "playwright install chromium")

    if args.probe:
        # Sonda: recorre todos los formularios en headless, rellena lo que
        # sabe y vuelca lo que NO sabe a preguntas-detectadas.md de cada
        # dossier + un resumen agregado. No hay input() ni clic en Enviar.
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            page = b.new_page()
            for i, row in enumerate(rows, 1):
                log(f"[sonda {i}/{len(rows)}] {row['empresa']} — "
                    f"{row.get('puesto','')[:44]}")
                try:
                    fill_one(page, row, dry_run=False, identity=identity)
                except Exception as exc:  # noqa: BLE001
                    log(f"    ✗ {type(exc).__name__}")
            b.close()
        log("\nSonda terminada. Revisa '· [select] sin respuesta definida' "
            "arriba y los preguntas-detectadas.md de cada carpeta.")
        return

    with sync_playwright() as pw:
        # headless=False a propósito: el objetivo es que lo veas y lo revises.
        browser = None
        if args.real_browser:
            # Chrome de verdad + perfil que persiste entre ejecuciones. El
            # Chromium de Playwright arranca sin cookies ni historial y con
            # navigator.webdriver=true; Cloudflare (Workable, Lever) lo puntúa
            # como bot y planta el challenge. Con el navegador real y un perfil
            # que acumula reputación, deja de saltar.
            os.makedirs(CHROME_PROFILE, exist_ok=True)
            log(f"    (Chrome real, perfil en {CHROME_PROFILE})")
            ctx = pw.chromium.launch_persistent_context(
                CHROME_PROFILE, channel="chrome", headless=False,
                no_viewport=True,
                args=["--start-maximized",
                      "--disable-blink-features=AutomationControlled"])
            # Playwright deja navigator.webdriver=true incluso con Chrome real,
            # y Turnstile lo puntúa. Esto sólo iguala el navegador a uno normal
            # para que TÚ puedas resolver el captcha a mano; no lo resuelve por
            # ti ni intenta saltárselo.
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined});")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
        else:
            browser = pw.chromium.launch(headless=False,
                                         args=["--start-maximized"])
            ctx = browser.new_context(no_viewport=True)
            page = ctx.new_page()
        auto_sent = 0
        auto_send_active = args.auto_send
        if not args.open_only:
            log(f"\nEn cualquier momento del relleno, pulsa "
                f"'{_SkipListener.KEY}' en esta terminal para descartar la "
                "oferta actual (senior, consultora, US-only...) y saltar a "
                "la siguiente sin esperar a que termine.")
        for i, row in enumerate(rows, 1):
            log(f"\n[{i}/{len(rows)}] {row['empresa']} — {row.get('puesto','')}")
            fill_failed = False
            skipped = False
            try:
                if args.open_only:
                    url = row.get("url") or _url_from_dossier(row["carpeta"])
                    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    log(f"    abierta: {url}")
                    log(f"    material en {row['carpeta']}/")
                else:
                    with _SkipListener() as skip:
                        fill_one(page, row, dry_run=False, skip=skip,
                                identity=identity)
            except SkipRequested:
                log("    ⏭  descartada a media ejecución — no volverá a "
                    "aparecer en fill")
                mark_state(row, "descartada")
                skipped = True
            except Exception as exc:  # noqa: BLE001
                log(f"    ✗ falló: {exc}")
                fill_failed = True

            if skipped:
                continue

            # Auto-envío opt-in: solo si --auto-send, el relleno no falló, no
            # es --open-only, y no se ha alcanzado el límite ni un envío
            # anterior en este mismo run ya salió ambiguo (en ese caso se para
            # el resto del batch de auto-envío, no el script entero — el resto
            # sigue en modo manual de toda la vida).
            if auto_send_active and not fill_failed and not args.open_only:
                if auto_sent >= args.auto_send_limit:
                    log(f"    (límite de auto-envío alcanzado: "
                        f"{args.auto_send_limit} — el resto, manual)")
                else:
                    resultado = try_auto_send(
                        page, row, dry_run_submit=args.dry_run_submit)
                    if resultado == "enviada":
                        auto_sent += 1
                        delay = args.auto_send_delay * random.uniform(0.8, 1.2)
                        if i < len(rows):
                            log(f"    (esperando {delay:.0f}s antes de la "
                                f"siguiente — evita parecer ráfaga de bot)")
                            time.sleep(delay)
                        continue  # ya gestionado y trackeado: sin input()
                    if resultado == "ambigua":
                        auto_send_active = False
                        log("    ⛔ paro el resto del auto-envío de este run "
                            "(estado ambiguo) — sigue en modo manual")

            # Aquí es donde se para: el envío lo decides tú.
            # Ctrl+C durante el input() lanza KeyboardInterrupt y antes soltaba
            # un traceback justo después de enviar la candidatura, dando la
            # impresión de que algo había fallado. Se trata como "salir".
            try:
                resp = input("\n    Revisa y ENVÍA en el navegador. "
                             "Enter = siguiente · 'm' = enviada · "
                             "'d' = descartar (senior/US/onsite...) · "
                             "'q' = salir: ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                log("\n    (interrumpido)")
                resp = "q"
            if resp == "m":
                mark_sent(row)
                log("    ✓ marcada como enviada en el tracker")
            elif resp == "d":
                mark_state(row, "descartada")
                log("    ✗ descartada — no volverá a aparecer en fill")
            if resp == "q":
                break
        log("\nCerrando navegador. Marca en applications/tracker.csv las que "
            "hayas enviado (columna 'estado' -> enviada).")
        try:
            # Con perfil persistente no hay objeto browser: se cierra el contexto.
            (browser or ctx).close()
        except Exception:  # noqa: BLE001
            # Si cerraste la ventana a mano, el driver ya no está y close()
            # lanza "Connection closed while reading from the driver". Es ruido
            # al final del proceso, no un fallo del envío.
            pass


if __name__ == "__main__":
    main()
