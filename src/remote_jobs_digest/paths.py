"""Where rjs reads its configuration and writes its data.

Config (what you edit):  $RJS_HOME, else $XDG_CONFIG_HOME/rjs, else ~/.config/rjs
    config.yaml    search profile (written by `rjs init`)
    profile.md     optional free-text narrative for the LLM judge
    identity.yaml  personal data for the optional `apply` extra
    .env           secrets (API keys, Telegram token)

Data (what rjs writes):  $RJS_DATA_DIR, else $XDG_DATA_HOME/rjs, else ~/.local/share/rjs
    output/        digests, CSV/JSON exports, caches ($RJS_OUTPUT_DIR overrides)
    boards/        ATS board lists built by `rjs boards`
    applications/  dossiers from the `apply` extra

Functions, not constants: tests and the CLI set the env vars after import.
"""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.getenv(var) or Path.home() / fallback)


def home() -> Path:
    return Path(os.getenv("RJS_HOME") or _xdg("XDG_CONFIG_HOME", ".config") / "rjs")


def data_dir() -> Path:
    return Path(os.getenv("RJS_DATA_DIR") or _xdg("XDG_DATA_HOME", ".local/share") / "rjs")


def config_file() -> Path:
    return Path(os.getenv("RJS_CONFIG") or home() / "config.yaml")


def narrative_file() -> Path:
    return home() / "profile.md"


def identity_file() -> Path:
    return home() / "identity.yaml"


def env_file() -> Path:
    return home() / ".env"


def output_dir() -> Path:
    return Path(os.getenv("RJS_OUTPUT_DIR") or data_dir() / "output")


def boards_dir() -> Path:
    return data_dir() / "boards"


def applications_dir() -> Path:
    return data_dir() / "applications"


def outreach_dir() -> Path:
    return data_dir() / "outreach"


def load_env_file() -> None:
    """Load KEY=VALUE lines from the .env in RJS_HOME without overriding the real env."""
    path = env_file()
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def display(path: str | os.PathLike) -> str:
    """Path for messages: $HOME shown as ~, so logs and demos stay short."""
    p, h = str(path), str(Path.home())
    return "~" + p[len(h):] if p == h or p.startswith(h + os.sep) else p
