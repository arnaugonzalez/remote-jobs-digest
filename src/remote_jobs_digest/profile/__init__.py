"""Interface pública del Module `profile` (design/CODEBASE-DESIGN.md §1).

Tres entradas: `load_profile(path)` (Fase 1c, en profile/loader.py),
`Profile.from_mapping(m)`, `Profile.defaults()`. Todo lo demás (sub-policies,
helpers de parseo) es Implementation.
"""

from .loader import load_profile
from .types import (
    CompanyKind, CompanyKindPolicy, Currency, ExperienceBand, GeoPolicy,
    GeoZone, Profile, ProfileError, RolePolicy, SalaryPolicy, SearchPolicy,
    SignalPolicy, StackCategory, StackPolicy, Verdict, WorkMode,
)

__all__ = [
    "CompanyKind", "CompanyKindPolicy", "Currency", "ExperienceBand",
    "GeoPolicy", "GeoZone", "load_profile", "Profile", "ProfileError",
    "RolePolicy", "SalaryPolicy", "SearchPolicy", "SignalPolicy",
    "StackCategory", "StackPolicy", "Verdict", "WorkMode",
]
