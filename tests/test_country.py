"""Uso fuera de Europa: ubicaciones por palabra completa, sufijo de título
solo si la ubicación es vaga, monedas no europeas, wizard con regiones y
LinkedIn/Indeed, y protección de LinkedIn frente a ubicaciones no reconocidas.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import remote_jobs_digest
from remote_jobs_digest import wizard
from remote_jobs_digest.classifier import Classifier, hint_in, location_text
from remote_jobs_digest.profile.loader import load_profile
from remote_jobs_digest.profile.schema import FIELDS
from remote_jobs_digest.profile.types import Profile
from remote_jobs_digest.sources import linkedin
from remote_jobs_digest.sources.base import Job

NZ_EXAMPLE = (Path(remote_jobs_digest.__file__).resolve().parent
              / "examples" / "frontend-fullstack-nz.yaml")


def _job(location: str, title: str = "Software Engineer", description: str = "") -> Job:
    return Job(source="test", title=title, company="acme",
               url="https://x.test/1", location=location, description=description)


@pytest.fixture
def clf_defaults() -> Classifier:
    return Classifier(Profile.defaults())


@pytest.fixture
def clf_nz() -> Classifier:
    return Classifier(load_profile(NZ_EXAMPLE))


@pytest.mark.parametrize("location, expected", [
    ("Princeton, NJ, USA", "us_only"),
    ("Albacete, Spain", "onsite_local"),
    ("Remote - Europe", "ok"),
])
def test_defaults_geo(clf_defaults, location, expected):
    assert clf_defaults.geo_verdict(_job(location)) == expected


def test_defaults_mainz_y_ukraine_no_son_hints(clf_defaults):
    assert clf_defaults.geo_verdict(_job("Mainz, Germany")) != "ok"
    assert clf_defaults.geo_verdict(_job("Kyiv, Ukraine")) != "remote_other_eu"


def test_sufijo_del_titulo_no_pisa_ubicacion_concreta(clf_defaults):
    job = _job("San Francisco, California",
               title="Senior Software Engineer - Distributed Data Systems")
    assert clf_defaults.geo_verdict(job) == "us_only"


def test_sufijo_del_titulo_cuenta_si_ubicacion_vaga(clf_defaults):
    assert clf_defaults.geo_verdict(_job("Remote", title="Backend Engineer - EMEA")) == "ok"


def test_location_text_ignora_sufijo_con_ubicacion_concreta():
    _, loc = location_text(_job("San Francisco", title="Engineer - US Remote"))
    assert loc == "san francisco"


def test_hint_palabra_completa():
    assert not hint_in(["cet"], "princeton")
    assert not hint_in(["nz"], "mainz")
    assert not hint_in(["uk"], "ukraine")
    assert hint_in(["cet"], "berlin cet")


def test_nz_geo(clf_nz):
    assert clf_nz.geo_verdict(_job("Auckland, New Zealand")) == "onsite_local"
    assert clf_nz.geo_verdict(_job("Remote - New Zealand")) == "ok"
    assert clf_nz.geo_verdict(_job("Remote - APAC")) == "ok"
    assert clf_nz.geo_verdict(_job("San Francisco")) != "ok"
    assert clf_nz.geo_verdict(_job("Berlin, Germany")) != "ok"


@pytest.mark.parametrize("text, currency", [
    ("NZ$120,000 - NZ$140,000", "NZD"),
    ("A$150k", "AUD"),
    ("$120,000", "USD"),
    ("€60.000", "EUR"),
])
def test_parse_salary_moneda(clf_defaults, text, currency):
    assert clf_defaults.parse_salary(text)[2] == currency


def test_wizard_pais_no_europeo(tmp_path):
    answers = {
        "geo.home_hints": "New Zealand, Auckland",
        "geo.region_hints": "apac, oceania",
        "salary.floor": "120000 NZD",
        "search.board_locations": "New Zealand",
    }
    io = wizard.WizardIO.scripted([answers.get(f.path, "") for f in FIELDS])
    dest = tmp_path / "config.local.yaml"
    profile = wizard.run(io, dest=dest)

    assert profile.salary.floor["NZD"] == 120000
    assert abs(profile.salary.floor["EUR"] - 61000) <= 2000
    assert {"linkedin", "indeed"} <= set(profile.search.active_sources)
    assert profile.search.board_queries == ("software engineer",)
    assert profile.geo.away_hints == ()
    assert "new zealand" not in profile.geo.blocked_hints


def test_wizard_moneda_desconocida_deja_suelo_por_defecto(tmp_path):
    answers = {"salary.floor": "100000 XYZ"}
    io = wizard.WizardIO.scripted([answers.get(f.path, "") for f in FIELDS])
    profile = wizard.run(io, dest=tmp_path / "config.local.yaml")
    assert profile.salary.floor == Profile.defaults().salary.floor


def test_linkedin_omite_ubicacion_no_reconocida(monkeypatch):
    def fake_search(term, location, *, easy, start):
        if start > 0:
            return []
        if location == "Auckland, New Zealand":
            # LinkedIn cae a EE.UU.: ninguna oferta dice estar en Auckland.
            return [_job("San Francisco, CA") for _ in range(6)]
        return [_job("Berlin, Germany")]

    monkeypatch.setattr(linkedin, "_search", fake_search)
    monkeypatch.setattr(linkedin, "_enrich_descriptions", lambda jobs: None)
    monkeypatch.setattr(linkedin.config, "LINKEDIN_LOCATIONS",
                        ["Auckland, New Zealand", "Worldwide"])
    monkeypatch.setattr(linkedin.config, "LINKEDIN_SEARCH_TERMS", ["frontend"])
    monkeypatch.setattr(linkedin.config, "LINKEDIN_PAGES", 2)
    monkeypatch.setattr(linkedin.config, "LINKEDIN_DELAY", 0)

    jobs = linkedin.fetch()

    assert [j.location for j in jobs] == ["Berlin, Germany"]
