# Changelog

## 0.2.0 — 2026-10-29

- Wizard asks for the wider regions whose remote jobs you can take (`geo.region_hints`) and for the country to search on LinkedIn/Indeed (`search.board_locations`, `search.board_queries`).
- Salary in NZD, AUD, CAD and CHF; the wizard derives the other currencies' floors and warn levels from the one you give.
- Locations match whole words, not substrings: fixes "Princeton", "Albacete" and "Gloucestershire" matching "cet"/"cest", "Mainz" matching "nz", and "Ukraine" matching "uk".
- A title suffix ("Engineer - US Remote") only counts when `location` is vague; "San Francisco" with a "... - Distributed Data Systems" title is no longer global.
- "distributed" is no longer a global hint by default (it stays a remote hint).
- LinkedIn skips a location it does not recognise (most results outside it) instead of silently returning US jobs.
- New example `frontend-fullstack-nz`.

## 0.1.1 — 2026-09-26

- PyPI page: README links to repo files (deploy templates, example profiles) now point to GitHub.
- Config messages show paths as `~/...` like the rest of the CLI.

## 0.1.0 — 2026-09-26

First public release.

- `rjs init` (interactive or `--from <example.yaml>`), `rjs run`, `rjs config check|show`, `rjs boards build|discover`, `rjs platforms`, `rjs paths`.
- Sources on by default: public ATS boards (Greenhouse, Ashby, Lever, SmartRecruiters, Recruitee, Breezy, Workable, Personio), Remote OK, Remotive, Himalayas, Working Nomads, NoDesk, HN "Who is hiring". Opt-in: LinkedIn (guest API), Indeed/Glassdoor/Google (via JobSpy), Built In, We Work Remotely.
- Declarative profile in YAML: location eligibility (home / region / away / blocked; also read from titles such as "ML Engineer - US Remote"), work modes, years band, weighted stack, salary floor per currency, consultancy filter.
- Bundled list of 43 intermediary platforms, each sourced from the company's own site, in four categories (talent marketplace, freelance marketplace, AI data work, reposting); each profile picks which categories to drop and can allow single names.
- Optional LLM pass over the survivors through any OpenAI-compatible endpoint.
- Digest to stdout and Markdown file; optional Telegram. Every job credits its source.
- `apply` extra (supervised pilot): dossiers, screening answers, form pre-fill, outreach drafts, inbox triage. Drafts only; nothing is sent or submitted for you.
