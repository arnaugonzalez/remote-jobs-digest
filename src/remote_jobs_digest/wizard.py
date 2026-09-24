"""Module `wizard` (design/CODEBASE-DESIGN.md §1, §3 "Interface del
wizard"). Pregunta campo a campo según `profile.schema.FIELDS` — la MISMA
tabla que usa `profile.loader` para saber qué existe (Seam 3): si el wizard
tuviera sus propias preguntas por su cuenta, la deriva entre wizard y loader
sería silenciosa hasta que alguien reportase "el wizard me preguntó X y el
scraper lo ignora".

El wizard NO importa `classifier`. Solo `profile.schema` y `Profile` — nunca
sabe qué hace el clasificador con las respuestas, solo que son válidas.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from remote_jobs_digest.profile.schema import FIELDS, FieldSpec
from remote_jobs_digest.profile.types import Profile, ProfileError


@dataclass
class WizardIO:
    """Seam de E/S del wizard. Un Adapter real (stdin/stdout) para
    `./rjs init`, y un Adapter de test (respuestas encoladas) para
    tests/test_wizard.py — sin esto, el wizard solo se puede probar a mano."""
    ask: Callable[[str, object], str]   # (pregunta, default) -> respuesta cruda
    say: Callable[[str], None]

    @classmethod
    def real(cls) -> "WizardIO":
        def ask(question: str, default: object) -> str:
            hint = f" [{_short_hint(default)}]" \
                if default not in (None, "", [], ()) else ""
            return input(f"{question}{hint}: ").strip()
        return cls(ask=ask, say=lambda msg: print(msg, flush=True))

    @classmethod
    def scripted(cls, answers: list[str]) -> "WizardIO":
        """Adapter de test: una cola de respuestas, una por pregunta, en el
        mismo orden que FIELDS. Lanza IndexError con mensaje claro si el
        wizard pregunta más de lo que el test previó — mejor eso que un
        cuelgue esperando stdin real."""
        queue = list(answers)
        said: list[str] = []

        def ask(question: str, default: object) -> str:
            if not queue:
                raise IndexError(
                    f"wizard preguntó más de lo esperado: {question!r} "
                    f"(sin respuesta encolada)")
            return queue.pop(0)

        return cls(ask=ask, say=said.append)


def _short_hint(default: object, limit: int = 4) -> str:
    """Texto del hint entre corchetes: un dict/list corto se muestra tal
    cual, uno largo se resume — volcar las ~20 frases de signals.negative
    en una sola línea de pregunta era ilegible en el uso real."""
    if isinstance(default, (list, tuple, frozenset, set)):
        items = list(default)
        if len(items) <= limit:
            return ", ".join(str(i) for i in items)
        return f"{', '.join(str(i) for i in items[:limit])}, … ({len(items)} total)"
    if isinstance(default, dict) and len(default) > limit:
        shown = dict(list(default.items())[:limit])
        return f"{shown}, … ({len(default)} total)"
    return str(default)


def _get_path(d: dict, path: str) -> object:
    cur = d
    for part in path.split("."):
        cur = cur[part]
    return cur


def _set_path(d: dict, path: str, value: object) -> None:
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _split_csv(raw: str) -> list[str]:
    # Minúsculas ya aquí, no solo en Profile.from_mapping: el chequeo de
    # solape home_hints/away_hints en run() compara contra el default (que
    # ya está en minúsculas) ANTES de validar — si no normalizamos ahora,
    # "Lisbon" (crudo) no coincide con "lisbon" (default) en esa comparación
    # aunque sí coincidan una vez from_mapping los normalice, y el solape
    # explota ahí en vez de limpiarse antes.
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _parse_answer(field: FieldSpec, raw: str, current_default: object) -> object:
    """raw ya viene .strip()-eada; vacío significa "quédate con el default
    actual" (que puede ser el de fábrica o el de `base` si el wizard se
    re-ejecuta sobre una config existente)."""
    if not raw:
        return current_default

    if field.kind == "int":
        try:
            return int(raw)
        except ValueError:
            return current_default

    if field.kind == "bool":
        return raw.lower() in ("y", "yes", "true", "1", "s", "si", "sí")

    if field.kind == "choice":
        val = raw.lower()
        return val if val in field.choices else current_default

    if field.kind == "list[str]":
        given = _split_csv(raw)
        if not given:
            return current_default
        if field.merge:
            merged = list(current_default) + [g for g in given
                                              if g not in current_default]
            return merged
        return given

    if field.kind == "list[choice]":
        given = [v for v in _split_csv(raw) if v.lower() in field.choices]
        return given or current_default

    if field.kind == "map[str,int]":
        # "40000" o "40000 EUR" — una sola cifra por pregunta, en la moneda
        # que ya encabeza el default; el resto de monedas del default no se
        # tocan (quick setup, no exige las 3 monedas de golpe).
        parts = raw.split()
        try:
            amount = int(parts[0].replace(",", "").replace(".", ""))
        except ValueError:
            return current_default
        currency = parts[1].upper() if len(parts) > 1 else (
            next(iter(current_default), "EUR"))
        out = dict(current_default)
        out[currency] = amount
        return out

    if field.kind == "text":
        return raw

    # "str" u otro kind no listado explícitamente: texto tal cual.
    return raw


def _ask_stack(io: WizardIO, field: FieldSpec, mapping: dict,
               current: list[dict]) -> None:
    """The stack is a weighted table in the YAML; the wizard asks for a flat
    list and turns it into one "core" category. Finer weighting (strong vs
    weak words, several categories) is done by editing config.yaml."""
    words_now = [w for cat in current for w in cat["strong"]]
    words = _split_csv(io.ask(field.question, words_now))
    if not words:
        return
    # "go", "r", "c" are ordinary English words/letters: counted only in the
    # title or tags, otherwise every posting that says "go" scores.
    strong = [w for w in words if len(w) > 2]
    weak = [w for w in words if len(w) <= 2]
    _set_path(mapping, "stack.weighted",
              [{"weight": 3, "label": "core", "strong": strong or words, "weak": weak}])
    _set_path(mapping, "stack.keywords", words)
    _set_path(mapping, "stack.high_signal", words)
    _set_path(mapping, "search.search_terms", words)


def run(io: WizardIO, *, dest: Path, base: Profile | None = None,
       include_identity: bool = True) -> Profile:
    """Pregunta campo a campo según `profile.schema.FIELDS`, valida con
    `Profile.from_mapping` ANTES de escribir, y escribe `dest` de un tirón.

    Invariantes (design/CODEBASE-DESIGN.md §3 "Interface del wizard"):
      - nunca escribe un fichero que el loader rechazaría (valida primero);
      - `base` permite re-ejecutarlo sobre una config existente — los
        defaults que se ofrecen son los valores ACTUALES, no los de fábrica;
      - la identidad (datos de autofill) es de otro Module (Fase 3) y no
        vive en este mapping ni en este fichero.
    """
    base = base or Profile.defaults()
    mapping = base.to_mapping()

    io.say(f"Setting up your search ({len(FIELDS)} questions, press Enter to "
           f"keep the value in brackets)...")
    for field in FIELDS:
        current = _get_path(mapping, field.path)
        if field.kind == "stack":
            _ask_stack(io, field, mapping, current)
            continue
        raw = io.ask(field.question, current)
        _set_path(mapping, field.path, _parse_answer(field, raw, current))

    # geo.home_hints y geo.away_hints/blocked_hints son roles RELATIVOS
    # entre sí (design/CODEBASE-DESIGN.md §5 D2), no listas independientes:
    # si el usuario reemplaza home_hints por "Germany", cualquier hint que
    # ahora coincida no puede seguir en away_hints/blocked_hints heredado
    # del default de otra persona — Profile.from_mapping ya rechaza ese
    # solape (I10), así que se limpia aquí antes de validar, no se le pide
    # al usuario que lo resuelva a mano.
    new_home = set(_get_path(mapping, "geo.home_hints"))
    if new_home:
        for other_path in ("geo.away_hints", "geo.blocked_hints"):
            current = _get_path(mapping, other_path)
            cleaned = [h for h in current if h not in new_home]
            if cleaned != current:
                _set_path(mapping, other_path, cleaned)

    try:
        profile = Profile.from_mapping(mapping)
    except ProfileError as exc:
        io.say("Not saved - the resulting config is not valid:")
        for path, msg in exc.problems:
            io.say(f"  · {path}: {msg}")
        raise

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        yaml.safe_dump(profile.to_mapping(), f, sort_keys=False,
                       allow_unicode=True, default_flow_style=False)
    try:
        dest.chmod(0o600)   # puede llevar keywords que delatan tu búsqueda
    except OSError:
        pass

    io.say(f"✓ wrote {dest}. Next: rjs run --no-ai")
    return profile


def render_example(profile: Profile | None = None) -> str:
    """Genera el texto de `config.example.yaml`: la MISMA estructura que
    escribiría el wizard, con el `help` de cada FieldSpec como comentario
    donde el schema lo declara (design/CODEBASE-DESIGN.md §5 D3 —
    config.example.yaml se GENERA, nunca se lee en runtime, y no debe
    divergir del shape real de Profile: por eso parte de
    Profile.defaults().to_mapping(), no de un literal escrito a mano)."""
    profile = profile or Profile.defaults()
    mapping = profile.to_mapping()
    help_by_path = {f.path: f.help for f in FIELDS}

    lines = [
        "# Example rjs config, generated from profile/schema.py.",
        "# Copy it to the path shown by `rjs paths` and edit it,",
        "# or run `rjs init` to answer the questions interactively.",
        "",
    ]
    for top_key, value in mapping.items():
        help_text = help_by_path.get(top_key)
        if help_text:
            lines.append(f"# {help_text}")
        block = yaml.safe_dump({top_key: value}, sort_keys=False,
                               allow_unicode=True, default_flow_style=False)
        lines.append(block.rstrip("\n"))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> None:
    """`rjs init`: interactive wizard, or `--from FILE` to copy and validate an
    existing profile (non-interactive: CI, cron boxes, Docker)."""
    import argparse
    import shutil
    import sys as _sys

    from remote_jobs_digest import paths
    from remote_jobs_digest.profile.loader import load_profile

    p = argparse.ArgumentParser(prog="rjs init", description="Set up your search profile")
    p.add_argument("--from", dest="from_file", default=None,
                   help="copy this YAML, or a bundled example by name "
                        "(see --list-examples), instead of asking")
    p.add_argument("--list-examples", action="store_true",
                   help="list the bundled example profiles")
    p.add_argument("--dest", default=None,
                   help="where to write (default: see `rjs paths`)")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing config when using --from")
    p.add_argument("--example", action="store_true",
                   help="print a documented example config to stdout and exit")
    args = p.parse_args(argv)

    if args.example:
        print(render_example(), end="")
        return

    examples = Path(__file__).resolve().parent / "examples"
    if args.list_examples:
        for f in sorted(examples.glob("*.yaml")):
            print(f"  {f.stem:<16} {f.read_text(encoding='utf-8').splitlines()[0].lstrip('# ')}")
        return
    if args.from_file and not Path(args.from_file).exists():
        bundled = examples / f"{args.from_file}.yaml"
        if bundled.exists():
            args.from_file = str(bundled)

    dest = Path(args.dest) if args.dest else paths.config_file()

    if args.from_file:
        if dest.exists() and not args.force:
            _sys.exit(f"{dest} already exists (use --force to overwrite)")
        try:
            load_profile(args.from_file)
        except ProfileError as exc:
            for path, msg in exc.problems:
                print(f"✗ {path}: {msg}", file=_sys.stderr)
            _sys.exit(1)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.from_file, dest)
        dest.chmod(0o600)
        print(f"✓ wrote {dest}. Next: rjs run --no-ai")
        return

    base = None
    if dest.exists():
        print(f"({dest} exists - your current answers are each question's "
              f"default; press Enter to keep them)")
        base = load_profile(dest)

    try:
        run(WizardIO.real(), dest=dest, base=base)
    except (ProfileError, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            print("\n(cancelled)")
        _sys.exit(1)


if __name__ == "__main__":
    main()
