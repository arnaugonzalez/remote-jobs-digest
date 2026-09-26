"""./rjs config --check / --show (design/CODEBASE-DESIGN.md §6 fase 5).

Reusa ProfileError.problems (Fase 1a) — la validación no vive aquí, vive en
Profile.from_mapping; este script solo la invoca y la imprime en un formato
legible antes de que el usuario descubra "0 ofertas" sin saber por qué.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from remote_jobs_digest import paths
from remote_jobs_digest.profile.loader import load_profile
from remote_jobs_digest.profile.types import Profile, ProfileError


def check(path: str | None = None) -> bool:
    """True if the config is valid; otherwise prints each problem with its field path."""
    p = Path(path) if path else paths.config_file()
    if not p.exists():
        print(f"✗ no config at {paths.display(p)}. Run `rjs init` "
              f"(or `rjs init --list-examples`).")
        return False
    try:
        with open(p, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise ProfileError([(str(p), "the YAML root must be a mapping")])
        Profile.from_mapping(raw)
    except ProfileError as exc:
        print(f"✗ {paths.display(p)} is not valid:")
        for field_path, msg in exc.problems:
            print(f"  · {field_path}: {msg}")
        return False
    except yaml.YAMLError as exc:
        print(f"✗ {paths.display(p)}: invalid YAML ({exc})")
        return False
    print(f"✓ {paths.display(p)} is valid.")
    return True


def show(path: str | None = None) -> None:
    """Print the EFFECTIVE config (file + profile.md narrative)."""
    profile = load_profile(path)
    print(yaml.safe_dump(profile.to_mapping(), sort_keys=False,
                         allow_unicode=True, default_flow_style=False))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="rjs config",
                                description="Validate or print your config")
    p.add_argument("action", choices=["check", "show"])
    p.add_argument("--config", default=None,
                   help="explicit YAML path (default: see `rjs paths`)")
    args = p.parse_args(argv)
    if args.action == "check":
        if not check(args.config):
            sys.exit(1)
    else:
        try:
            show(args.config)
        except ProfileError as exc:
            for field_path, msg in exc.problems:
                print(f"✗ {field_path}: {msg}")
            sys.exit(1)
