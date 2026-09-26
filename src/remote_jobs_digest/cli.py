"""`rjs`: the command-line entry point.

Subcommands import their module lazily, so `rjs init` and `rjs config` work
before a config exists and the optional `apply` extra is only needed when used.
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
from importlib.resources import files

from remote_jobs_digest import __version__, paths

USAGE = f"""rjs {__version__}: a daily digest of remote jobs you are eligible for.

usage: rjs <command> [options]

core
  init      create your profile (interactive, or --from backend-ai-eu)
  run       collect, filter and print today's digest (--no-ai: zero tokens)
  config    check | show your config
  boards    build | discover: grow the list of company ATS boards to scan
  platforms list the bundled marketplaces / AI-data-work platforms and which you skip
  paths     print where config and data live

apply extra (supervised pilot, pip install 'remote-jobs-digest[apply]')
  apply     build application dossiers (letter, answers) for the top matches
  verify    re-read full postings with the LLM and prune the queue
  answer    draft answers to free-text screening questions
  fill      pre-fill ATS forms in a real browser; you review and submit
  outreach  draft (never send) cold emails to companies
  gmail     classify recruiter replies in your inbox

Run `rjs <command> --help` for options.
"""

# command -> (module, needs a valid config before importing)
CORE = {
    "init": ("remote_jobs_digest.wizard", False),
    "config": ("remote_jobs_digest.config_check", False),
    "run": ("remote_jobs_digest.scraper", True),
    "boards-build": ("remote_jobs_digest.boards.build_companies", True),
    "boards-discover": ("remote_jobs_digest.boards.discover_ats", True),
}
APPLY = {
    "apply": "remote_jobs_digest.apply.apply_kit",
    "verify": "remote_jobs_digest.apply.verify_kit",
    "answer": "remote_jobs_digest.apply.answer_kit",
    "fill": "remote_jobs_digest.apply.autofill",
    "outreach": "remote_jobs_digest.apply.outreach",
    "gmail": "remote_jobs_digest.apply.gmail_watch",
}

PILOT_WARNING = (
    "⚠️  `rjs {cmd}` is part of the supervised-pilot `apply` extra: it drafts, "
    "you review and send.\n    Automating applications may break job sites' "
    "terms of service. Nothing is submitted or sent without you.\n"
)


def _print_paths() -> None:
    rows = [
        ("config", paths.config_file()), ("narrative", paths.narrative_file()),
        ("identity", paths.identity_file()), ("env", paths.env_file()),
        ("output", paths.output_dir()), ("boards", paths.boards_dir()),
    ]
    for name, path in rows:
        mark = "✓" if path.exists() else "·"
        print(f"  {mark} {name:<9} {path}")


def _print_platforms() -> None:
    from remote_jobs_digest import platforms
    from remote_jobs_digest.profile.loader import load_profile
    from remote_jobs_digest.profile.types import ProfileError
    try:
        company = load_profile().company
        skip, allow = company.skip_platforms, {a.lower() for a in company.allow_platforms}
    except ProfileError:
        skip, allow = platforms.CATEGORIES, set()
        print("(no config yet: showing the defaults, every category skipped)\n")
    for category in platforms.CATEGORIES:
        state = "skipped" if category in skip else "kept"
        print(f"{category} [{state}]")
        for p in platforms.bundled():
            if p.category == category:
                mark = "·" if category not in skip or p.name.lower() in allow else "✗"
                print(f"  {mark} {p.name:<22} {p.source}")
    print("\nChange with company.skip_platforms / allow_platforms in your config.")


def _require_config() -> None:
    from remote_jobs_digest.profile.types import ProfileError
    from remote_jobs_digest.profile.loader import load_profile
    try:
        load_profile()
    except ProfileError as exc:
        for field_path, msg in exc.problems:
            print(f"✗ {field_path}: {msg}", file=sys.stderr)
        sys.exit(2)


def _call(module: str, argv: list[str], prog: str) -> None:
    mod = importlib.import_module(module)
    if inspect.signature(mod.main).parameters:
        mod.main(argv)
    else:
        # Modules whose main() still reads sys.argv directly.
        sys.argv = [prog, *argv]
        mod.main()


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE, end="")
        return
    if argv[0] in ("-V", "--version"):
        print(__version__)
        return

    paths.load_env_file()
    cmd, rest = argv[0], argv[1:]

    if cmd == "paths":
        _print_paths()
        return
    if cmd == "platforms":
        _print_platforms()
        return
    if cmd == "boards":
        if not rest or rest[0] not in ("build", "discover"):
            sys.exit("usage: rjs boards build [--fetch] | rjs boards discover [--limit N]")
        cmd, rest = f"boards-{rest[0]}", rest[1:]

    if cmd in CORE:
        module, needs_config = CORE[cmd]
        if needs_config and {"-h", "--help"} & set(rest) and not paths.config_file().exists():
            # Modules read the profile on import; --help must work before `rjs init`.
            os.environ["RJS_CONFIG"] = str(files("remote_jobs_digest") / "examples" / "backend-ai-eu.yaml")
        elif needs_config:
            _require_config()
        _call(module, rest, f"rjs {cmd.replace('-', ' ')}")
        return

    if cmd in APPLY:
        print(PILOT_WARNING.format(cmd=cmd), file=sys.stderr)
        _require_config()
        try:
            _call(APPLY[cmd], rest, f"rjs {cmd}")
        except ModuleNotFoundError as exc:
            sys.exit(f"`rjs {cmd}` needs the apply extra ({exc.name} missing): "
                     f"pip install 'remote-jobs-digest[apply]'")
        return

    sys.exit(f"unknown command: {cmd!r}. Run `rjs --help`.")


if __name__ == "__main__":
    main()
