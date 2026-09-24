"""identity.py — Module separado de `profile` (design/CODEBASE-DESIGN.md §1
"identity, separado por tres razones"). Verifica las tres: clase de secreto
distinta (fichero propio), ausencia legítima (empty() sin fallar), y que
field_map() es una función de `identity`, no un derivado congelado.
"""

from __future__ import annotations



import pytest

from remote_jobs_digest.apply.identity import Identity, IdentityError, field_map, load_identity


def test_empty_no_falla_sin_fichero(tmp_path):
    """Ausencia legítima (razón 2): quien no tiene identidad no debe ver
    una excepción, solo campos vacíos."""
    ident = load_identity(tmp_path / "no-existe.yaml")
    assert ident == Identity.empty()
    assert ident.email == ""


def test_fichero_valido(tmp_path):
    f = tmp_path / "identity.local.yaml"
    f.write_text("first_name: Ana\nemail: ana@example.com\n", encoding="utf-8")
    ident = load_identity(f)
    assert ident.first_name == "Ana"
    assert ident.email == "ana@example.com"
    assert ident.phone == ""  # no declarado, se queda vacío


def test_clave_desconocida_se_ignora_sin_fallar():
    """A diferencia de Profile (donde una clave desconocida es error,
    porque una errata en criterios de búsqueda da '0 ofertas' sin causa
    visible), un campo de identidad desconocido es mucho más inofensivo:
    simplemente no hay ningún field_map() que lo use. Se ignora."""
    ident = Identity.from_mapping({"first_name": "Ana", "campo_raro": "x"})
    assert ident.first_name == "Ana"


def test_fichero_roto_lanza_identity_error(tmp_path):
    """Distinto de 'no existe': un fichero presente pero mal formado no
    debe degradar en silencio a campos vacíos — eso rellenaría formularios
    en blanco sin que nadie entienda por qué."""
    f = tmp_path / "identity.local.yaml"
    f.write_text("- esto\n- es\n- una lista, no un mapa\n", encoding="utf-8")
    with pytest.raises(IdentityError):
        load_identity(f)


def test_yaml_invalido_lanza_identity_error(tmp_path):
    f = tmp_path / "identity.local.yaml"
    f.write_text("first_name: [sin cerrar\n", encoding="utf-8")
    with pytest.raises(IdentityError):
        load_identity(f)


def test_field_map_con_identity_vacia_no_rellena_nada():
    """La guarda ya existente en autofill.py (`if not value: return
    False`) es lo que hace que Identity.empty() sea segura — aquí solo
    verificamos que field_map() produce valores vacíos, no que el
    formulario no se toque (eso es cosa de autofill.py)."""
    fm = field_map(Identity.empty())
    assert all(value == "" for _, _, value, _ in fm)
    assert len(fm) == 16  # 16 campos de identidad conocidos


def test_field_map_no_es_un_derivado_congelado():
    """Dos identidades distintas producen dos field_maps distintos en la
    misma ejecución — si field_map fuera una lista de módulo construida en
    import (el bug original de FIELD_MAP), esto sería imposible."""
    a = field_map(Identity(first_name="Ana"))
    b = field_map(Identity(first_name="Bruno"))
    a_name = next(v for n, _, v, _ in a if n == "Nombre")
    b_name = next(v for n, _, v, _ in b if n == "Nombre")
    assert a_name == "Ana"
    assert b_name == "Bruno"


def test_round_trip_to_mapping_from_mapping():
    ident = Identity(first_name="Ana", email="ana@example.com",
                     linkedin="https://linkedin.com/in/ana")
    assert Identity.from_mapping(ident.to_mapping()) == ident
