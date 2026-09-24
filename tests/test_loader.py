"""profile.loader — Adapter de disco. Solo se testea "encuentra el fichero /
falla bien" (design/CODEBASE-DESIGN.md §2 Seam 1); la validación en sí ya
está cubierta en test_profile.py contra Profile.from_mapping.
"""

from __future__ import annotations

import pytest

from remote_jobs_digest.profile.loader import load_profile
from remote_jobs_digest.profile.types import ProfileError


def test_sin_config_pide_rjs_init(tmp_path, monkeypatch):
    # No built-in profile: a fresh install must not silently filter jobs
    # for somebody else's profile.
    monkeypatch.setenv("RJS_HOME", str(tmp_path))
    with pytest.raises(ProfileError) as exc:
        load_profile()
    assert "rjs init" in str(exc.value.problems)


def test_config_por_env_rjs_config(tmp_path, monkeypatch):
    f = tmp_path / "mine.yaml"
    f.write_text("experience: {ideal_min: 5, ideal_max: 8, stretch: 9, max_required: 10}\n",
                 encoding="utf-8")
    monkeypatch.setenv("RJS_CONFIG", str(f))
    assert load_profile().experience.ideal_min == 5


def test_path_explicito_inexistente_lanza_profile_error(tmp_path):
    with pytest.raises(ProfileError):
        load_profile(tmp_path / "no-existe.yaml")


def test_path_explicito_valido(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(
        "experience:\n  ideal_min: 1\n  ideal_max: 2\n  stretch: 3\n"
        "  max_required: 4\n"
        "geo:\n  home_hints: [germany]\n  accept_modes: [remote]\n"
        "narrative: hola\n",
        encoding="utf-8")
    p = load_profile(f)
    assert p.experience.ideal_min == 1
    assert p.geo.home_hints == ("germany",)
    assert p.narrative == "hola"


def test_yaml_invalido_lanza_profile_error_con_ruta_de_campo(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("experience:\n  ideal_min: 9\n  ideal_max: 1\n", encoding="utf-8")
    with pytest.raises(ProfileError) as exc:
        load_profile(f)
    assert exc.value.problems  # al menos un (path, mensaje)


def test_yaml_raiz_no_es_mapa(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("- esto\n- es\n- una lista\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(f)
