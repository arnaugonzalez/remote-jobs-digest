"""Golden test of the EXAMPLE profile (backend-ai-eu defaults): same verdicts,
oferta por oferta, que produjo `filters.py` ORIGINAL sobre los mismos 28
casos sintéticos (tests/golden/cases.py). El snapshot
(tests/golden/snapshot.json) se generó UNA VEZ contra el repo privado, antes
de escribir `classifier.py` — no se regenera contra el propio classifier,
o el test dejaría de significar nada.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from remote_jobs_digest.classifier import Classifier
from remote_jobs_digest.profile.types import Profile
from remote_jobs_digest.sources.base import Job
from tests.golden.cases import CASES

HERE = Path(__file__).resolve().parent

with open(HERE / "golden" / "snapshot.json", encoding="utf-8") as f:
    SNAPSHOT: dict = json.load(f)

# The package ships no consultancy list; the consultancy case needs one name.
_BASE = Profile.defaults()
CLF = Classifier(replace(_BASE, company=replace(
    _BASE.company, consulting_companies=("accenture",))))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_mismo_verdict_que_el_original(case):
    expected = SNAPSHOT[case["id"]]
    job = Job(source="golden", title=case["title"], company=case["company"],
              url=case.get("url", "https://x.test/1"), location=case["location"],
              description=case["description"],
              salary_text=case.get("salary_text", ""))
    CLF.process(job)
    actual = {
        "verdict": job.verdict, "geo": job.geo, "level": job.level,
        "salary_flag": job.salary_flag, "years_required": job.years_required,
        "is_consulting": job.is_consulting, "has_exclusivity": job.has_exclusivity,
        "stack_score": job.stack_score,
    }
    assert actual == expected, (
        f"{case['id']}: classifier.py discrepa de filters.py original\n"
        f"  esperado: {expected}\n"
        f"  obtenido: {actual}")


def test_cobertura_snapshot_completa():
    """Todos los casos definidos hoy en cases.py tienen su entrada en el
    snapshot — si alguien añade un caso sin regenerar el snapshot, este test
    lo dice claro en vez de un KeyError críptico dentro del parametrize."""
    ids_casos = {c["id"] for c in CASES}
    ids_snapshot = set(SNAPSHOT.keys())
    assert ids_casos == ids_snapshot, (
        f"desincronizado — en cases.py y no en snapshot.json: "
        f"{ids_casos - ids_snapshot}; en snapshot.json y no en cases.py: "
        f"{ids_snapshot - ids_casos}")


@pytest.mark.parametrize("title,geo", [
    ("Open-Source Machine Learning Engineer - US Remote", "us_only"),
    ("Machine Learning Engineer - EMEA Remote", "ok"),
    ("Backend Engineer (Europe)", "ok"),
    ("Backend Engineer - Platform", "remote_unclear"),
])
def test_region_in_title_when_location_is_bare_remote(title, geo):
    job = Job(source="t", title=title, company="c", url="https://x.test/1",
              location="Remote", description="")
    assert CLF.geo_verdict(job) == geo


@pytest.mark.parametrize("title,geo", [
    ("Open-Source Machine Learning Engineer - US Remote", "us_only"),
    ("Machine Learning Engineer - EMEA Remote", "ok"),
])
def test_run_path_reads_region_in_title(title, geo):
    from remote_jobs_digest import filters
    job = Job(source="t", title=title, company="c", url="https://x.test/1",
              location="Remote", description="")
    assert filters.geo_verdict(job) == geo
