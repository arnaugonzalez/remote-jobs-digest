"""Bundled platform list: data integrity, whole-word matching, per-profile choice."""
from __future__ import annotations

import pytest

from remote_jobs_digest import platforms
from remote_jobs_digest.platforms import CATEGORIES, bundled, match
from remote_jobs_digest.profile.types import CompanyKindPolicy


def test_every_entry_is_sourced_and_unique():
    names = [p.name.lower() for p in bundled()]
    assert len(names) == len(set(names))
    patterns = [m for p in bundled() for m in p.match]
    assert len(patterns) == len(set(patterns))
    for p in bundled():
        assert p.category in CATEGORIES
        assert p.match and all(m == m.lower() for m in p.match)
        assert p.source.startswith("https://"), p.name


def test_every_category_has_entries():
    assert {p.category for p in bundled()} == set(CATEGORIES)


@pytest.mark.parametrize("company", ["Toptal", "Lemon.io", "Proxify AB", "UPWORK"])
def test_known_platforms_match(company):
    assert match(company, CATEGORIES) is not None


@pytest.mark.parametrize("company", [
    "Happen Inc",                      # contains "appen"
    "Recruitment and Contracting Ltd", # contains "contra"
    "Archer (Malta)",                  # contains "malt"
    "Stripe",
])
def test_substrings_do_not_match(company):
    assert match(company, CATEGORIES) is None


def test_skip_is_per_category():
    freelance = next(p for p in bundled() if p.category == "freelance_marketplace")
    others = tuple(c for c in CATEGORIES if c != "freelance_marketplace")
    assert match(freelance.name, others) is None
    assert match(freelance.name, ("freelance_marketplace",)) == (
        freelance.name, "freelance_marketplace")


def test_allow_keeps_one_platform_of_a_skipped_category():
    assert match("Toptal", CATEGORIES, allow=("toptal",)) is None
    assert match("Lemon.io", CATEGORIES, allow=("toptal",)) is not None


def test_extra_names_are_custom():
    assert match("Acme Talent Cloud", (), extra=("acme talent",)) == ("acme talent", "custom")


def test_missing_key_defaults_to_every_category():
    problems: list = []
    c = CompanyKindPolicy.from_mapping({"kind": "any"}, "company", problems)
    assert c.skip_platforms == CATEGORIES and not problems


def test_unknown_category_is_reported():
    problems: list = []
    c = CompanyKindPolicy.from_mapping(
        {"skip_platforms": ["ai_data_work", "recruiters"]}, "company", problems)
    assert c.skip_platforms == ("ai_data_work",)
    assert problems and problems[0][0] == "company.skip_platforms"


def test_cli_lists_platforms(capsys):
    from remote_jobs_digest.cli import main
    main(["platforms"])
    out = capsys.readouterr().out
    assert all(c in out for c in CATEGORIES)
    assert platforms.bundled()[0].name in out
