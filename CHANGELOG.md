# Changelog

## 0.1.1 — unreleased

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
