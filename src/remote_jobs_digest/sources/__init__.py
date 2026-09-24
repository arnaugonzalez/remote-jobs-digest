"""Registro de fuentes. scraper.py itera ACTIVE_SOURCES y llama a fetch()."""

from . import remoteok, remotive, himalayas, ats, hackernews, linkedin
from . import jobspy_boards, workingnomads, nodesk, builtin

REGISTRY = {
    "remoteok": remoteok.fetch,
    "remotive": remotive.fetch,
    "himalayas": himalayas.fetch,
    "ats": ats.fetch,
    "hackernews": hackernews.fetch,
    "linkedin": linkedin.fetch,
    "workingnomads": workingnomads.fetch,
    "nodesk": nodesk.fetch,
    "builtin": builtin.fetch,
    # Vía python-jobspy (dependencia opcional; se omiten con aviso si falta):
    "indeed": jobspy_boards.fetch_indeed,
    "glassdoor": jobspy_boards.fetch_glassdoor,
    "google": jobspy_boards.fetch_google,
}

# We Work Remotely está retirado (exige suscripción de pago para aplicar) y es
# el único módulo que necesita `feedparser`. Se importa de forma tolerante para
# que la falta de esa dependencia no rompa todo el proyecto.
try:
    from . import weworkremotely
    REGISTRY["weworkremotely"] = weworkremotely.fetch
except ImportError:  # pragma: no cover — feedparser no instalado
    pass
