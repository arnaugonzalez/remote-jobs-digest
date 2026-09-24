"""Tests de los invariantes documentados en design/CODEBASE-DESIGN.md §3
(I1..I12). Fase 1a de docs/OSS-PLAN.md: nadie más usa `profile` todavía, así
que estos tests son la única superficie que puede romperse aquí.
"""

from __future__ import annotations



import pytest

from remote_jobs_digest.profile.types import Profile, ProfileError


# --- I8: defaults viven en código, sin disco --------------------------------

def test_defaults_no_toca_disco(monkeypatch):
    # Si defaults() abriera un fichero, esto lo delataría.
    def _boom(*a, **kw):
        raise AssertionError("Profile.defaults() no debe tocar el filesystem")
    monkeypatch.setattr("builtins.open", _boom)
    p = Profile.defaults()
    assert p.experience.ideal_min == 2


def test_defaults_es_determinista():
    assert Profile.defaults() == Profile.defaults()


# --- I7: round-trip ----------------------------------------------------------

def test_round_trip_defaults():
    p = Profile.defaults()
    assert Profile.from_mapping(p.to_mapping()) == p


def test_round_trip_mapping_minimo_valido():
    m = {
        "experience": {"ideal_min": 1, "ideal_max": 3, "stretch": 4, "max_required": 5},
        "salary": {"floor": {"EUR": 30_000}, "warn": {"EUR": 20_000},
                  "floor_junior": {}, "warn_junior": {}},
        "stack": {"required": [], "weighted": [], "min_score": 0},
        "geo": {"home_hints": ["germany"], "accept_modes": ["remote"]},
        "role": {},
        "company": {"kind": "any"},
        "signals": {},
        "narrative": "hola",
        "search": {"search_terms": ["python"], "active_sources": ["ats"]},
    }
    p = Profile.from_mapping(m)
    assert Profile.from_mapping(p.to_mapping()) == p


# --- I4: inmutabilidad --------------------------------------------------------

def test_profile_es_frozen():
    p = Profile.defaults()
    with pytest.raises(AttributeError):
        p.narrative = "otra cosa"  # type: ignore[misc]


# --- I2 + I3: validación agregada, no primero-que-falla ----------------------

def test_multiples_errores_se_agregan_todos():
    m = {
        "experience": {"ideal_min": 5, "ideal_max": 2},  # orden invertido
        "salary": {"floor": {"EUR": 10_000}, "warn": {"EUR": 20_000}},  # warn > floor
        "stack": {"weighted": [{"weight": 1, "label": "x", "strong": ["a"]}],
                  "min_score": 100},  # inalcanzable
        "geo": {"home_hints": ["spain"], "away_hints": ["spain"]},  # solape
        "clave_rara": 1,  # desconocida
    }
    with pytest.raises(ProfileError) as exc:
        Profile.from_mapping(m)
    paths = {p for p, _ in exc.value.problems}
    assert "experience" in paths
    assert "salary.warn.EUR" in paths
    assert "stack.min_score" in paths
    assert "geo" in paths
    assert "clave_rara" in paths
    # Las 5 categorías de error deben estar TODAS, no solo la primera.
    assert len(exc.value.problems) >= 5


def test_clave_desconocida_es_error_en_modo_strict():
    with pytest.raises(ProfileError):
        Profile.from_mapping({"salario": {}})  # errata: en español, no existe


def test_clave_desconocida_se_ignora_sin_strict():
    p = Profile.from_mapping({"salario": {}}, strict=False)
    assert p == Profile.defaults().__class__.from_mapping({}, strict=False)


# --- I10: invariantes cross-field concretos ----------------------------------

def test_experience_fuera_de_orden_falla():
    with pytest.raises(ProfileError):
        Profile.from_mapping({
            "experience": {"ideal_min": 5, "ideal_max": 2, "stretch": 1,
                           "max_required": 1},
        })


def test_geo_home_y_away_solapados_falla():
    with pytest.raises(ProfileError):
        Profile.from_mapping({
            "geo": {"home_hints": ["germany"], "away_hints": ["germany"]},
        })


def test_geo_onsite_sin_home_hints_falla():
    with pytest.raises(ProfileError):
        Profile.from_mapping({
            "geo": {"home_hints": [], "accept_modes": ["onsite"]},
        })


def test_stack_min_score_inalcanzable_falla():
    with pytest.raises(ProfileError):
        Profile.from_mapping({
            "stack": {"weighted": [{"weight": 2, "label": "x", "strong": ["a"]}],
                     "min_score": 10},
        })


def test_salario_warn_mayor_que_floor_falla():
    with pytest.raises(ProfileError):
        Profile.from_mapping({
            "salary": {"floor": {"EUR": 30_000}, "warn": {"EUR": 40_000}},
        })


# --- I11: thesis() interpola umbrales -----------------------------------------

def test_thesis_interpola_umbrales_reales():
    p = Profile.defaults()
    t = p.thesis()
    assert "40,000EUR" in t.replace(" ", "") or "40.000" in t or "40,000" in t
    assert str(p.experience.ideal_min) in t
    assert str(p.experience.max_required) in t
