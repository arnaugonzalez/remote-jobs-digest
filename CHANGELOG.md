# Changelog

## 0.1.0 — unreleased

First public release.

- `rjs init` (interactive or `--from <example.yaml>`), `rjs run`, `rjs config check|show`, `rjs boards build|discover`, `rjs paths`.
- Sources on by default: public ATS boards (Greenhouse, Ashby, Lever, SmartRecruiters, Recruitee, Breezy, Workable, Personio), Remote OK, Remotive, Himalayas, Working Nomads, NoDesk, HN "Who is hiring". Opt-in: LinkedIn (guest API), Indeed/Glassdoor/Google (via JobSpy), Built In, We Work Remotely.
- Declarative profile in YAML: location eligibility (home / region / away / blocked), work modes, years band, weighted stack, salary floor per currency, consultancy and staffing-marketplace filters.
- Optional LLM pass over the survivors through any OpenAI-compatible endpoint.
- Digest to stdout and Markdown file; optional Telegram. Every job credits its source.
- `apply` extra (supervised pilot): dossiers, screening answers, form pre-fill, outreach drafts, inbox triage. Drafts only; nothing is sent or submitted for you.
