"""Casos sintéticos que cubren las ramas de decisión de filters.classify().
Usado dos veces: (1) contra el filters.py ORIGINAL para capturar el
comportamiento "antes"; (2) contra el nuevo Classifier para verificar
"mismos verdicts oferta por oferta" (design/CODEBASE-DESIGN.md §6, fase 1b).
"""

CASES = [
    # --- Geografía ---
    dict(id="geo_spain_remote_ok",
         title="Backend Engineer", company="Acme",
         location="Remote - Spain", description="FastAPI, Python, 3+ years"),
    dict(id="geo_spain_onsite_local",
         title="Backend Engineer", company="Acme",
         location="Barcelona, Spain", description="FastAPI, Python, 3+ years"),
    dict(id="geo_germany_remote_other_eu",
         title="Backend Engineer", company="Acme",
         location="Remote - Germany", description="FastAPI, Python, 3+ years"),
    dict(id="geo_germany_onsite",
         title="Backend Engineer", company="Acme",
         location="Berlin, Germany", description="FastAPI, Python, 3+ years"),
    dict(id="geo_us_city_only",
         title="Backend Engineer", company="Acme",
         location="San Francisco, CA", description="FastAPI, Python, 3+ years"),
    dict(id="geo_us_only_but_hires_globally",
         title="Backend Engineer", company="Acme",
         location="US Only", description=(
             "FastAPI, Python, 3+ years. We hire globally, open to "
             "international candidates.")),
    dict(id="geo_remote_bare",
         title="Backend Engineer", company="Acme",
         location="Remote", description="FastAPI, Python, 3+ years"),
    dict(id="geo_remote_latam",
         title="Backend Engineer", company="Acme",
         location="Remote - LATAM", description="FastAPI, Python, 3+ years"),
    dict(id="geo_remote_worldwide",
         title="Backend Engineer", company="Acme",
         location="Remote - Worldwide", description=(
             "FastAPI, Python, 3+ years. We hire globally.")),
    dict(id="geo_india",
         title="Backend Engineer", company="Acme",
         location="Bengaluru, India", description="FastAPI, Python, 3+ years"),
    dict(id="geo_unknown_no_location",
         title="Backend Engineer", company="Acme",
         location="", description="FastAPI, Python, 3+ years"),

    # --- Salario (salary_text es un campo del Job aparte de description;
    #     enrich_salary() lee de ahí, no del cuerpo del anuncio) ---
    dict(id="salary_ok",
         title="Backend Engineer", company="Acme",
         location="Remote - Spain", description="FastAPI, Python, 3+ years",
         salary_text="$70,000 - $90,000"),
    dict(id="salary_low",
         title="Backend Engineer", company="Acme",
         location="Remote - Spain", description="FastAPI, Python, 3+ years",
         salary_text="$50,000"),
    dict(id="salary_below_floor",
         title="Backend Engineer", company="Acme",
         location="Remote - Spain", description="FastAPI, Python, 3+ years",
         salary_text="$30,000"),
    dict(id="salary_unrecognized_currency",
         title="Backend Engineer", company="Acme",
         location="Remote - Spain", description="FastAPI, Python, 3+ years",
         salary_text="60,000 PLN"),

    # --- Nivel / años ---
    dict(id="level_overqualified_senior",
         title="Senior Backend Engineer", company="Acme",
         location="Remote - Spain",
         description="FastAPI, Python, 8+ years of experience required"),
    dict(id="level_underqualified_intern",
         title="Backend Engineering Intern", company="Acme",
         location="Remote - Spain", description="FastAPI, Python"),
    dict(id="level_ideal_mid",
         title="Backend Engineer", company="Acme",
         location="Remote - Spain",
         description="FastAPI, Python, 3+ years of experience required"),
    dict(id="level_junior_range",
         title="Backend Engineer", company="Acme",
         location="Remote - Spain",
         description="FastAPI, Python, 0-2 years of experience"),
    dict(id="years_stretch",
         title="Backend Engineer", company="Acme",
         location="Remote - Spain",
         description="FastAPI, Python, 5+ years of experience required"),
    dict(id="years_over_max",
         title="Backend Engineer", company="Acme",
         location="Remote - Spain",
         description="FastAPI, Python, 8+ years of experience required. "
                     "Not a senior title."),

    # --- Modelo de empresa / plataformas ---
    dict(id="company_consulting_known",
         title="Backend Engineer", company="Accenture",
         location="Remote - Spain", description="FastAPI, Python, 3+ years"),
    dict(id="company_staffing_platform",
         title="Backend Engineer", company="Toptal",
         location="Remote - Spain", description="FastAPI, Python, 3+ years"),
    dict(id="company_paywalled_domain",
         title="Backend Engineer", company="Acme",
         location="Remote - Spain", description="FastAPI, Python, 3+ years",
         url="https://weworkremotely.com/jobs/123"),

    # --- Rol / señales negativas ---
    dict(id="role_not_engineering",
         title="Sales Account Executive", company="Acme",
         location="Remote - Spain", description="FastAPI, Python, 3+ years"),
    dict(id="signal_negative_us_citizen",
         title="Backend Engineer", company="Acme",
         location="Remote - Spain",
         description="FastAPI, Python, 3+ years. Must be a US citizen."),

    # --- Stack ("Software Engineer" sí es ROLE_SIGNAL pero ni "software" ni
    #     "engineer" están en ninguna keyword strong/weak de STACK_WEIGHTS,
    #     así que pasa is_engineering_role() con stack_score=0) ---
    dict(id="stack_below_threshold",
         title="Software Engineer", company="Acme",
         location="Remote - Spain",
         description="We are a growing company looking for someone "
                     "versatile, 3+ years of experience required."),

    # --- Exclusividad (no descarta, solo marca) ---
    dict(id="exclusivity_flagged",
         title="Backend Engineer", company="Acme",
         location="Remote - Spain",
         description="FastAPI, Python, 3+ years. Full-time exclusivity "
                     "required, no side projects. Salary: $70,000"),
]
