"""Léxico compartido de detección textual — no es parte del Profile del
usuario (DOMAIN-MODEL.md ADR-0001: separar *perfil* de *léxico*).

Un código de estado de EE.UU. o el nombre de una ciudad estadounidense
significan lo mismo para cualquier usuario, viva donde viva: son evidencia de
que una oferta ancla la residencia a EE.UU., no una preferencia que el
wizard deba preguntar. Por eso viven aquí, fuera de `Profile`, y `classifier`
los usa como constantes fijas en vez de campos editables.
"""

from __future__ import annotations

# Los ATS no siempre escriben "United States": ponen la ciudad de la oficina
# ("San Francisco, CA"). Se detecta por sufijo de estado (", CA") y por
# nombre de ciudad, que es como los escriben en la práctica.
US_STATE_CODES: tuple[str, ...] = (
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
)

US_CITIES: tuple[str, ...] = (
    "san francisco", "new york", "seattle", "austin", "boston", "chicago",
    "los angeles", "san jose", "palo alto", "mountain view", "denver",
    "atlanta", "miami", "washington", "san diego", "portland", "dallas",
    "houston", "philadelphia", "phoenix", "pittsburgh", "nashville",
    "salt lake city", "boulder", "bellevue", "redmond", "sunnyvale",
    "santa clara", "menlo park", "cambridge ma", "brooklyn",
)

# Tokens que, tras trocear la ubicación por separadores comunes, marcan CADA
# trozo como "de EE.UU." (is_us_only exige que TODOS los trozos lo sean).
US_ONLY_TOKENS: tuple[str, ...] = (
    "united states", "usa", "u.s.", "us-only", "us only",
    "us based", "us-based",
)

# "Remote US", "Remote - Canada": remoto, pero restringido a un continente
# concreto — no es un país-fuera-de-la-región genérico, es Norteamérica
# específicamente, y el patrón de escritura varía (guion, paréntesis, coma).
REMOTE_BUT_NORTH_AMERICA: tuple[str, ...] = (
    "remote us", "remote-us", "remote (us", "remote, us", "remote usa",
    "remote united states", "remote canada", "remote - canada",
    "remote north america", "remote (north america", "remote americas",
    "us remote", "usa remote", "canada remote", "north america remote",
)
