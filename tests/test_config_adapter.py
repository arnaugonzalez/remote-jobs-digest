"""config.py is a thin adapter: every profile constant it exposes derives
exactly from the loaded Profile (here: the example profile set up in
conftest.py), so existing `config.X` consumers do not notice the source.
"""

from __future__ import annotations

from remote_jobs_digest import config
from remote_jobs_digest.profile.loader import load_profile

P = load_profile()


def test_stack_deriva_de_profile():
    assert config.TECH_KEYWORDS == list(P.stack.keywords)
    assert config.HIGH_SIGNAL_KEYWORDS == list(P.stack.high_signal)
    assert config.MIN_STACK_SCORE == P.stack.min_score
    assert [w[0] for w in config.STACK_WEIGHTS] == [c.weight for c in P.stack.weighted]
    assert [w[1] for w in config.STACK_WEIGHTS] == [c.label for c in P.stack.weighted]


def test_geo_deriva_de_profile():
    assert config.SPAIN_HINTS == list(P.geo.home_hints)
    assert config.EU_REGION_HINTS == list(P.geo.region_hints)
    assert config.EU_COUNTRY_HINTS == list(P.geo.away_hints)
    assert config.NON_EU_COUNTRY_HINTS == list(P.geo.blocked_hints)
    assert config.REMOTE_HINTS == list(P.geo.remote_hints)
    assert config.EU_LOCATION_HINTS == (config.SPAIN_HINTS
                                        + config.EU_REGION_HINTS
                                        + config.EU_COUNTRY_HINTS)


def test_experiencia_deriva_de_profile():
    assert config.IDEAL_YEARS == (P.experience.ideal_min, P.experience.ideal_max)
    assert config.STRETCH_YEARS == P.experience.stretch
    assert config.MAX_YEARS_REQUIRED == P.experience.max_required


def test_salario_deriva_de_profile():
    assert config.MIN_SALARY == dict(P.salary.floor)
    assert config.WARN_SALARY == dict(P.salary.warn)
    assert config.MIN_SALARY_JUNIOR == dict(P.salary.floor_junior)
    assert config.WARN_SALARY_JUNIOR == dict(P.salary.warn_junior)


def test_rol_y_empresa_derivan_de_profile():
    assert config.ROLE_SIGNALS == list(P.role.role_signals)
    assert config.NON_ROLE_SIGNALS == list(P.role.non_role_signals)
    assert config.ALLOWED_LEVELS == set(P.role.allowed_levels)
    assert config.CONSULTING_COMPANIES == list(P.company.consulting_companies)
    assert config.STAFFING_PLATFORMS == list(P.company.staffing_platforms)


def test_entorno_operacion_no_depende_de_profile():
    """Estas SIGUEN siendo os.getenv puro (design/CODEBASE-DESIGN.md §1,
    'No-seam deliberado') — no deben derivar de Profile jamás."""
    assert isinstance(config.LINKEDIN_HOURS, int)
    assert isinstance(config.AI_MODEL, str)
    assert isinstance(config.HTTP_TIMEOUT, float)


def test_filters_process_sigue_funcionando_sin_tocarlo():
    """El consumidor más importante (filters.py, NO modificado en esta
    fase) sigue leyendo `import config` con éxito y produce un Job
    clasificado — regresión de humo del Adapter completo."""
    from remote_jobs_digest import filters
    from remote_jobs_digest.sources.base import Job

    job = Job(source="test", title="Backend Engineer", company="Acme",
              url="https://x.test/1", location="Remote - Spain",
              description="FastAPI, Python, 3+ years")
    filters.process(job)
    assert job.verdict in ("APTA", "REVISAR", "DESCARTADA")
