"""Disk adapter, thin on purpose: everything worth testing lives in
`Profile.from_mapping`. Here we only find the file, read it and pass it on.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

import yaml

from remote_jobs_digest import paths

from .types import Profile, ProfileError


def load_profile(path: str | os.PathLike[str] | None = None, *,
                 env: Mapping[str, str] | None = None) -> Profile:
    """Read the user's config and return a validated Profile.

    path=None -> $RJS_CONFIG, else <RJS_HOME>/config.yaml (see paths.py).
    There is no built-in profile: without a config file this raises
    ProfileError telling the user to run `rjs init`, so nobody silently
    gets results filtered for somebody else's profile.

    env -> reserved for RJS_* overrides of the effective Profile.

    Raises ProfileError if the file is missing or invalid; never returns
    a half-built Profile.
    """
    config_path = Path(path) if path is not None else paths.config_file()
    if not config_path.exists():
        raise ProfileError([(str(config_path),
                             "no config file found - run `rjs init` "
                             "(or `rjs init --list-examples`)")])

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, Mapping):
        raise ProfileError([(str(config_path),
                             f"the YAML root must be a mapping, got "
                             f"{type(raw).__name__}")])
    profile = Profile.from_mapping(raw)

    narrative_path = paths.narrative_file()
    if not profile.narrative and narrative_path.exists():
        from dataclasses import replace
        profile = replace(profile,
                          narrative=narrative_path.read_text(encoding="utf-8"))
    return profile
