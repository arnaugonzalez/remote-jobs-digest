# Security

Please report vulnerabilities privately through GitHub's "Report a vulnerability"
(Security tab) rather than a public issue. Expect a reply within a week.

rjs keeps your profile, optional identity data and API keys under `~/.config/rjs/`
(`rjs init` writes `config.yaml` with mode 600; do the same for `identity.yaml` and `.env`).
Outbound traffic: GET requests to the job sources, job descriptions plus your profile text
to the LLM endpoint you configure (if any), and the digest to Telegram (if configured).
No telemetry.
