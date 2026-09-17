# SuperDL documentation map

This directory is the engineering source of truth: architecture and hard constraints, per-module contracts and invariants, the UI/UX spec, the copy style guide and the decision log.
Deployment guides and runbooks for operators live under [`deploy/`](../deploy/README.md).

## What to read

| You want to know                                                                              | Read                                                                             |
| --------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| How the system fits together and the constraints that must not break                          | [architecture.md](./architecture.md)                                             |
| A module's tables, endpoints, roles and rules                                                 | [reference/](./reference/) (one page per module: 数据模型 / 契约 / 规则与不变量) |
| Quotas, rate limits, retention periods                                                        | [reference/limits.md](./reference/limits.md)                                     |
| Page shapes and interactions                                                                  | [ui-ux-spec.md](./ui-ux-spec.md)                                                 |
| How user-visible copy is written, banned words                                                | [copy-style-guide.md](./copy-style-guide.md)                                     |
| Cross-module decisions and their constraints                                                  | [decisions.md](./decisions.md)                                                   |
| Hard rules, gates, commit conventions                                                         | [../CLAUDE.md](../CLAUDE.md)                                                     |
| Onboarding and the PR workflow                                                                | [../CONTRIBUTING.md](../CONTRIBUTING.md)                                         |
| Production deployment, releases, database requirements                                        | [../deploy/README.md](../deploy/README.md)                                       |
| Cluster installation, the two tiers, north-south ingress and Gateway API CRDs, token rotation | [../deploy/cluster/README.md](../deploy/cluster/README.md)                       |
| What to do first when an alert fires                                                          | [../deploy/cluster/runbooks/](../deploy/cluster/runbooks/README.md)              |
| How to report a security vulnerability                                                        | [../SECURITY.md](../SECURITY.md)                                                 |

### Module references (`reference/`)

| File                                                 | Scope                                                                                                                                     |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| [account.md](./reference/account.md)                 | Registration and login, JWT sessions, SSH keys, identity verification, account deletion                                                   |
| [catalog.md](./reference/catalog.md)                 | SKUs, platform image catalog, approximate inventory                                                                                       |
| [orchestrator.md](./reference/orchestrator.md)       | Instance state machine, outbox orchestration, reconciler, SSH / JupyterLab access, K8s abstraction                                        |
| [services.md](./reference/services.md)               | Online-service aggregate: data model and derived state, deploy / stop / delete contracts, domain rules, gateway API-key auth, rate limits |
| [disks.md](./reference/disks.md)                     | Data-disk lifecycle, quotas, expansion and daily settlement                                                                               |
| [images.md](./reference/images.md)                   | Image catalog management, in-cluster P2P cache and per-node prewarming                                                                    |
| [billing.md](./reference/billing.md)                 | Wallet, ledger, hourly settlement, subscription and spot pricing, arrears reclamation, policy parameters                                  |
| [payment.md](./reference/payment.md)                 | Top-up orders, payment channels (registry, Stripe, WeChat Pay, Alipay), callbacks and lookups, refunds and invoices                       |
| [metering.md](./reference/metering.md)               | Prometheus proxy queries, usage_hourly aggregation, reconciliation                                                                        |
| [nodes.md](./reference/nodes.md)                     | One-command node enrollment, node ledger, cluster capability probing                                                                      |
| [notify.md](./reference/notify.md)                   | In-app messages, SMS, Alertmanager webhook, low-balance warnings                                                                          |
| [tickets.md](./reference/tickets.md)                 | Ticket conversation flow and stale-ticket patrol                                                                                          |
| [legal.md](./reference/legal.md)                     | Legal document version flow and registration consent records                                                                              |
| [platform-config.md](./reference/platform-config.md) | Admin online configuration center, encrypted storage, deployment identity                                                                 |
| [security.md](./reference/security.md)               | Production boot validation, rate-limit layers, tenant isolation, accepted trade-offs                                                      |
| [observability.md](./reference/observability.md)     | Metrics, logs, probes, alert rules, admin-drawn monitoring                                                                                |
| [i18n.md](./reference/i18n.md)                       | Copy and internationalization mechanics and gates                                                                                         |
| [limits.md](./reference/limits.md)                   | Quotas, rate limits, clocks and retention periods in one place                                                                            |
| [admin.md](./reference/admin.md)                     | Admin console contracts and role boundaries                                                                                               |
| [web.md](./reference/web.md)                         | User console routes and frontend invariants                                                                                               |

## Maintenance conventions

- **Docs travel with code in the same commit:** when an endpoint, table, role, default, patrol period, command or procedure changes, update the matching reference / runbook / README in that commit.
- **Facts only, no rationale:** a reference records "what is" and "what must not break"; no argument, history, review numbers, change log or dates. Cross-module decisions go to [decisions.md](./decisions.md), each entry stating only the decision and its constraints.
- **Numbers must be traceable to code:** quotas, periods and defaults name the file or configuration key that holds them (e.g. `SETTING_SPECS` in `platform_config.py`, `SUPERDL_*`).
- **References must exist:** relative links and back-ticked repository paths are checked by `python3 scripts/check-docs-links.py` (including alert-rule `runbook_url` files and anchors); the CI `docs` job runs the same check.
- **Fixed structure for module references:** `数据模型` → `契约` (endpoint / role / description table) → `规则与不变量`; modules without their own tables omit `数据模型`; `limits.md` is the cross-module summary. New modules follow this template and are registered in the table above.
- **Language:** new content is English. Section headings of the existing Chinese references keep their names until each page is translated (links and anchors depend on them); Chinese punctuation conventions in those pages stay as they are until then.
