"""Module `identity` (design/CODEBASE-DESIGN.md §1 "separado por tres
razones, no por gusto"):

1. Clase de secreto distinta de `profile` — se teclea en formularios de
   terceros. Fichero propio (identity.local.yaml), entrada propia de
   .gitignore.
2. Ausencia legítima: quien solo quiere el scraper no tiene identidad.
   `Identity.empty()` deja todos los campos en "" y la guarda que YA existe
   en autofill.py (`if not value: return False`) los salta sola — sin
   KeyError, sin caso especial.
3. Dos Adapters reales en este seam (identidad real / vacía): por la regla
   "un adapter es un seam hipotético, dos es uno real", el seam está
   justificado.

No importa `profile` ni `classifier`: un usuario puede tener criterios de
búsqueda sin identidad, o viceversa, y eso tiene que compilar sin ciclos.
"""

from __future__ import annotations

from dataclasses import dataclass, fields as dc_fields
from pathlib import Path
from typing import Mapping

import yaml

DEFAULT_FILENAME = "identity.local.yaml"


@dataclass(frozen=True, slots=True)
class Identity:
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    preferred_name: str = ""
    email: str = ""
    phone: str = ""
    current_company: str = ""
    current_title: str = ""
    current_location: str = ""
    country: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    university: str = ""
    degree_result: str = ""
    entrance_exam: str = ""
    # Screening answers (free text, answered as-is when a form asks).
    citizenship: str = ""
    work_authorization: str = ""
    needs_sponsorship: str = ""
    remote_policy: str = ""
    notice_period: str = ""
    salary_expectation: str = ""
    years_experience: str = ""
    degree: str = ""
    pronouns: str = ""

    @classmethod
    def empty(cls) -> "Identity":
        return cls()

    @classmethod
    def from_mapping(cls, m: Mapping[str, object]) -> "Identity":
        known = {f.name for f in dc_fields(cls)}
        kwargs = {k: ("" if v is None else str(v))
                 for k, v in m.items() if k in known}
        return cls(**kwargs)

    def to_mapping(self) -> dict[str, str]:
        return {f.name: getattr(self, f.name) for f in dc_fields(self)}


class IdentityError(Exception):
    """El fichero existe pero no se pudo leer — distinto de 'no existe'
    (razón 2 del docstring del módulo: ausencia legítima no es error, un
    fichero roto sí lo es. Si no distinguimos los dos, un YAML mal escrito
    rellena formularios en blanco sin que nadie se entere)."""


def load_identity(path: str | Path | None = None) -> Identity:
    """Sin fichero -> Identity.empty() (ausencia legítima, no falla).
    Con fichero pero inválido -> IdentityError (para no rellenar
    formularios en blanco en silencio)."""
    if path is not None:
        p = Path(path)
    else:
        from remote_jobs_digest import paths
        p = paths.identity_file()

    if not p.exists():
        return Identity.empty()

    with open(p, encoding="utf-8") as f:
        try:
            raw = yaml.safe_load(f) or {}
        except yaml.YAMLError as exc:
            raise IdentityError(f"{p}: YAML inválido ({exc})") from exc

    if not isinstance(raw, Mapping):
        raise IdentityError(
            f"{p}: la raíz debe ser un mapa, es {type(raw).__name__}")

    return Identity.from_mapping(raw)


# ---------------------------------------------------------------------------
# field_map: la estructura (regex de etiqueta, selectores CSS de respaldo)
# es genérica de los ATS (Greenhouse/Lever/Ashby) y es el activo reutilizable
# para cualquier usuario — se queda fija. Solo el VALOR sale de `identity`.
# Antes era FIELD_MAP, una lista de módulo de autofill.py construida en
# import (design/CODEBASE-DESIGN.md §5 D4-ii: eso es el mismo "derivado
# congelado" que _US_STATE_RE — un test que cambia la identidad no vería el
# cambio). Ahora es una función: field_map(identity).
# ---------------------------------------------------------------------------

def field_map(identity: Identity) -> list[tuple[str, str, str, list[str]]]:
    """(nombre, patrón de ETIQUETA, valor, selectores CSS de respaldo).

    La etiqueta manda porque es lo único común a todos los ATS:
    `get_by_label()` resuelve `<label for>`, `aria-label` y
    `aria-labelledby` a la vez — Greenhouse etiqueta con `aria-label` y
    Ashby con `<label for>`. Los selectores CSS quedan como red de
    seguridad para campos sin etiqueta accesible.
    """
    return [
        ("Nombre", r"^first name|^nombre|given name", identity.first_name,
         ["input#first_name", "input#_systemfield_name",
          "input[autocomplete='given-name']"]),
        ("Apellidos", r"^last name|^apellido|surname|family name",
         identity.last_name,
         ["input#last_name", "input[autocomplete='family-name']"]),
        ("Nombre completo", r"^full name|^name$", identity.full_name, []),
        ("Nombre preferido", r"preferred (first )?name",
         identity.preferred_name, []),
        ("Email", r"^e-?mail", identity.email,
         ["input#email", "input#_systemfield_email", "input[type='email']"]),
        ("Teléfono", r"^phone|^tel|mobile number", identity.phone,
         ["input#phone", "input[type='tel']"]),
        ("Empresa actual", r"current (company|employer)",
         identity.current_company, ["input#currentCompany"]),
        ("Puesto actual", r"current (title|role|position|job title)",
         identity.current_title, ["input#currentTitle"]),
        ("Ubicación actual", r"current location|where are you "
                             r"(currently )?based|city|location",
         identity.current_location, ["input#currentLocation"]),
        ("País", r"^country", identity.country, ["input#country"]),
        ("LinkedIn", r"linkedin", identity.linkedin,
         ["input#LinkedIn", "input[name*='linkedin' i]"]),
        ("GitHub", r"github", identity.github,
         ["input#GitHub", "input[name*='github' i]"]),
        ("Portfolio/Web", r"portfolio|personal website|^website",
         identity.portfolio, ["input#Portfolio", "input[name*='website' i]"]),
        ("Universidad", r"which university|what university|university did|"
                        r"educational institution", identity.university, []),
        ("Nota carrera", r"degree result|university grade|expected result",
         identity.degree_result, []),
        ("Acceso univ.", r"sat score|sat or equivalent|entrance exam|"
                         r"national exams|selectividad",
         identity.entrance_exam, []),
    ]
