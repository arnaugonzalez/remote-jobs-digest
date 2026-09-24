"""Every default source parses a recorded, sanitised API response into Jobs
without touching the network. Fixtures keep each API's real shape; titles,
companies, URLs and descriptions are synthetic."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from remote_jobs_digest.sources import ats, hackernews, himalayas, nodesk, remoteok, remotive, workingnomads

FIX = Path(__file__).parent / "fixtures"

# URL substring -> fixture file
ROUTES = {
    "remoteok.com/api": "remoteok.json",
    "remotive.com/api": "remotive.json",
    "himalayas.app/jobs/api": "himalayas.json",
    "workingnomads.com/api": "workingnomads.json",
    "nodesk.co/remote-jobs/index.xml": "nodesk.xml",
    "hn.algolia.com/api/v1/search": "hn_search.json",
    "hn.algolia.com/api/v1/items": "hn_item.json",
    "boards-api.greenhouse.io": "greenhouse.json",
    "api.ashbyhq.com": "ashby.json",
}


def fake_get(url, *, params=None, headers=None, retries=2):
    for needle, name in ROUTES.items():
        if needle in url:
            return httpx.Response(200, content=(FIX / name).read_bytes(),
                                  request=httpx.Request("GET", url))
    raise AssertionError(f"unexpected network call: {url}")


@pytest.mark.parametrize("module", [remoteok, remotive, himalayas, workingnomads,
                                    nodesk, hackernews], ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_source_parses_fixture(module, monkeypatch):
    monkeypatch.setattr(module, "http_get", fake_get)
    jobs = module.fetch()
    assert jobs, "no jobs parsed"
    for job in jobs:
        assert job.title and job.url and job.source
        assert job.id


def test_hackernews_skips_empty_comments(monkeypatch):
    monkeypatch.setattr(hackernews, "http_get", fake_get)
    jobs = hackernews.fetch()
    assert [j.company for j in jobs] == ["Acme Remote Inc", "Beta Labs"]
    assert "remote" in jobs[0].location.lower()


@pytest.mark.parametrize("parser,slug", [(ats._greenhouse, "acme"), (ats._ashby, "acme")])
def test_ats_parsers(parser, slug, monkeypatch):
    monkeypatch.setattr(ats, "http_get", fake_get)
    jobs = parser("Acme Remote Inc", slug)
    assert jobs and all(j.company == "Acme Remote Inc" and j.url for j in jobs)
    assert all(j.source.startswith("ats:") for j in jobs)


def test_remoteok_skips_legal_notice(monkeypatch):
    monkeypatch.setattr(remoteok, "http_get", fake_get)
    first = json.loads((FIX / "remoteok.json").read_text())[0]
    assert "legal" in first
    assert len(remoteok.fetch()) == 3
