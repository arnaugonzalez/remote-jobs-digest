"""Intermediary platforms: companies whose postings are not a direct employer's role.

The bundled list (data/platforms.yaml) tags each platform with one category;
which categories to drop is a profile choice (`company.skip_platforms`). Names
match the posting's company as whole words, so "appen" never hits "Happen Inc";
ambiguous names ("Turing", "Crossover") only match the whole company field.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

import yaml

CATEGORIES = (
    "talent_marketplace",      # vetted network placing engineers with client companies
    "freelance_marketplace",   # open marketplace, anyone bids for gigs
    "ai_data_work",            # paid AI-training / annotation / evaluation tasks
    "reposting_intermediary",  # reposts other companies' roles
)


@dataclass(frozen=True, slots=True)
class Platform:
    name: str
    category: str
    match: tuple[str, ...]   # whole words anywhere in the company field
    exact: tuple[str, ...]   # the whole company field, for ambiguous names
    source: str


@cache
def bundled() -> tuple[Platform, ...]:
    text = files("remote_jobs_digest").joinpath("data/platforms.yaml").read_text("utf-8")
    out = []
    for e in yaml.safe_load(text)["platforms"]:
        if e["category"] not in CATEGORIES:
            raise ValueError(f"platforms.yaml: {e['name']}: unknown category {e['category']!r}")
        out.append(Platform(e["name"], e["category"], tuple(e.get("match", ())),
                            tuple(e.get("exact", ())), e["source"]))
    return tuple(out)


# Both are used with fullmatch() on the lowercased company field.
def _word(pattern: str) -> re.Pattern[str]:
    return re.compile(rf".*(?<![a-z0-9]){re.escape(pattern.lower())}(?![a-z0-9]).*", re.S)


def _whole(name: str) -> re.Pattern[str]:
    return re.compile(rf"\s*{re.escape(name.lower())}\s*")


@cache
def _rules(skip: tuple[str, ...], extra: tuple[str, ...],
           allow: tuple[str, ...]) -> tuple[tuple[re.Pattern[str], str, str], ...]:
    allowed = {a.lower() for a in allow}
    rules = [(rx, p.name, p.category)
             for p in bundled()
             if p.category in skip and p.name.lower() not in allowed
             and not allowed.intersection(p.match + p.exact)
             for rx in [*map(_word, p.match), *map(_whole, p.exact)]]
    rules += [(_word(e), e, "custom") for e in extra if e.lower() not in allowed]
    return tuple(rules)


def match(company: str, skip: tuple[str, ...], extra: tuple[str, ...] = (),
          allow: tuple[str, ...] = ()) -> tuple[str, str] | None:
    """(platform name, category) if `company` is a platform to drop, else None."""
    low = company.lower()
    for rx, name, category in _rules(skip, extra, allow):
        if rx.fullmatch(low):
            return name, category
    return None
