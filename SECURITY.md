# Security policy

## Reporting a vulnerability

- Report privately; do not open a public issue and do not put vulnerability details in commit messages or PR descriptions. Prefer GitHub's private vulnerability reporting (repository Security → Advisories); if that is unavailable, contact the repository maintainers directly.
- Include: the affected component (api / web / admin / deploy / node-join.sh / instance images), reproduction steps and your assessment of the impact. Redact credentials and tenant data.
- Handling: we acknowledge the report, state the intended fix, and ship it with the next `main` merge and release. There is no bug bounty.

## Supported versions

- Only `main` is supported; there are no release branches. Deployments run images built from tags (`.github/workflows/release.yml`).
- Test only against instances you operate yourself. Never target other tenants, production data or third parties (payment, SMS, CAPTCHA or identity-verification providers).

## Design and hardening

- Security design, tenant isolation, rate-limit layers and accepted trade-offs: `docs/reference/security.md`.
- Production boot validation (secrets, providers, domain placeholders fail fast): the first rule in that document's "规则与不变量". <!-- cjk-ok -->
- Credentials never enter git: they are injected through environment variables or the platform configuration center, and every template uses `CHANGE_ME` (`deploy/app/secrets.example.yaml`).
- Supply chain: CI runs gitleaks over the full commit history, pip-audit and pnpm audit (HIGH+ blocks); release images are scanned with Trivy and shipped with an SBOM.
