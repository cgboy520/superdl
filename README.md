# SuperDL

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](./LICENSE)

SuperDL is a GPU rental platform: tenants rent whole-GPU, MIG or shared-GPU container instances (SSH + JupyterLab) that are metered per second and billed per hour; operators get one-command node enrollment, SKU and inventory management, financial reconciliation, tickets and an alerting loop.

Backend: FastAPI modular monolith + PostgreSQL 18. Frontends: React 19 + antd 6 (user console and admin console). Platform: RKE2 / k3s with Kata and HAMi.

**Status:** pre-release. The code is feature-complete; going live still depends on validation against real hardware and onboarding with the payment / SMS / identity providers you choose.

## Documentation map

Full index: [docs/README.md](docs/README.md). Most used:

- [docs/architecture.md](docs/architecture.md) — architecture, module boundaries, data model, core flows and hard constraints
- [docs/reference/](docs/README.md) — per-module contracts and invariants
- [docs/decisions.md](docs/decisions.md) — cross-module decisions and their constraints
- [CLAUDE.md](CLAUDE.md) — engineering rules, gates and commit conventions; onboarding and the PR workflow are in [CONTRIBUTING.md](CONTRIBUTING.md)
- [deploy/README.md](deploy/README.md) — deployment, releases, cluster installation and runbooks
- [SECURITY.md](SECURITY.md) — reporting security vulnerabilities

## Quick start

Prerequisites: Docker, uv, Python 3.13, Node 24, pnpm 11 (versions in `.python-version`, `package.json` and the CI workflow).
Backend commands start from the repository root; run the API and the worker in separate terminals under `apps/api`. `seed_dev.py` is for dev/test only: it creates SKUs, platform images and an administrator whose password is printed once (`SUPERDL_SEED_ADMIN_PASSWORD` pins it).

```bash
docker compose -f deploy/app/compose.yaml up -d

cd apps/api
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run python scripts/seed_dev.py
uv run uvicorn app.main:app --reload
uv run python -m app.workers.main
```

Frontends run from the repository root, one terminal each for web and admin. API docs at `http://localhost:8000/docs`, web at `http://localhost:5173`, admin at `http://localhost:5174`; sign in with the seeded administrator.

```bash
pnpm install
pnpm --filter web dev
pnpm --filter admin dev
```

Kubernetes defaults to `FakeOrchestrator` (in-process, in-memory); set `SUPERDL_K8S_BACKEND=real` to talk to a real cluster. This is independent of `SUPERDL_ENVIRONMENT` (prod refuses `fake`).

## Deployment

Production deployment, releases, cluster installation (RKE2 "full" or k3s "light"), node enrollment and runbooks are documented under [deploy/README.md](deploy/README.md). The deployment identity — `SUPERDL_COMPLIANCE_PROFILE`, `SUPERDL_PLATFORM_CURRENCY`, `SUPERDL_BILLING_TIMEZONE` — is environment-only and required in prod (see [docs/reference/platform-config.md](docs/reference/platform-config.md)).

## Internationalization and regions

- Consoles ship in English (default) and Simplified Chinese; the error catalog's source language is English (`apps/api/app/core/messages.py`) with a hand-maintained zh-CN translation. See [docs/reference/i18n.md](docs/reference/i18n.md).
- Defaults are region-neutral: email is the login handle, USD / UTC billing, Stripe Checkout, official k3s / rke2 installer hosts, generic invoice tax IDs. Mainland-China integrations (phone-required registration, Aliyun CAPTCHA / SMS / real-name verification, WeChat Pay / Alipay, PRC invoice tax IDs, the rancher-mirror installer source) are opt-in under `compliance_profile=cn` and the matching provider settings — nothing is hard-wired to a region.

## Gates

Run the local gates that match your change; do not push red. A PR merges only with a fully green CI. The single list of commands lives in [CLAUDE.md](CLAUDE.md) ("常用命令" and "工作流与提交约定").

## Repository layout

| Directory             | Contents                                                                                                                                                                                |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/api`            | FastAPI modular monolith (account / catalog / orchestrator / services / billing / metering / notify / nodes / legal / tickets / adminapi); one image, two entry points (serve / worker) |
| `apps/web`            | User console (React 19 + antd 6, light theme)                                                                                                                                           |
| `apps/admin`          | Admin console (same stack, dark NOC theme)                                                                                                                                              |
| `packages/api-client` | orval-generated fetchers and model types from `openapi.json` (never hand-edited)                                                                                                        |
| `packages/ui`         | Shared theme tokens, status maps, formatting helpers and shared copy for both consoles                                                                                                  |
| `deploy/`             | ansible control-plane installation, cluster helmfile and runbooks, platform K8s manifests and local compose, node-join tests, instance images                                           |
| `e2e/`                | Playwright browser smoke tests                                                                                                                                                          |
| `docs/`               | Architecture, module references, UI/UX spec, copy style guide, decision log                                                                                                             |
| `scripts/`            | Release scripts and repository-level gate scripts (banned words, CJK, CSP hash, doc references, gateway manifests)                                                                      |

## License

Apache-2.0 — see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
