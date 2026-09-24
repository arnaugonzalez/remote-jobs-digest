# Contributing

```bash
git clone https://github.com/arnaugonzalez/remote-jobs-digest && cd remote-jobs-digest
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
ruff check src tests && pytest
```

- Tests never touch the network. A new source needs a sanitised fixture in `tests/fixtures/`
  (real API shape, synthetic titles/companies/URLs) and a case in `tests/test_sources.py`.
- Changing the classifier? `tests/test_classifier_golden.py` pins the example profile's verdicts.
  If a verdict changes on purpose, say why in the PR.
- Only add sources you can reach without logging in, and read their terms first. Anything that
  scrapes a site whose terms forbid it must stay opt-in.
- Consultancy names belong in user config, not in the package. The only built-in company list is
  talent marketplaces (template posts, not roles).
