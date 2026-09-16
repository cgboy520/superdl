# deploy

| Directory | Contents |
|---|---|
| `app/` | Deployment of the platform itself: local compose (PG18) + production K8s manifests (`k8s/`: API / worker / frontends / gateway and TLS / RBAC / migration Job / PG backup CronJob) + frontend image (`frontend.Dockerfile` + nginx) |
| `ansible/` | Initial control-plane install (`site.yml`, the [servers] group as rke2/k3s server: audit policy, server config rendering, installer sha256 verification before install, plus the cluster state backup cron on k3s) and host hardening (`harden.yml`, [servers] + [agents]: key-only sshd, default-deny nftables, account cleanup, sysctl). GPU nodes join through the admin console "Add node" one-shot command (node-join.sh), not through ansible |
| `cluster/` | Cluster component helmfile (RKE2/k3s + Cilium + GPU Operator + HAMi + kube-prometheus-stack + Rook-Ceph + TopoLVM + Envoy Gateway + Loki/Alloy); the full/light tiers and version pins are in `cluster/README.md`; the Gateway API CRDs are managed in one place by `cluster/gateway-api-crds.sh` (called from the helmfile presync); `cluster/admission/` holds the seven VAP admission policies (not a helm release: `cluster/apply.sh` applies and reads them back before helmfile, `cluster/preflight.sh` and `scripts/release.sh` each assert once more that all of them are Deny) |

The platform code does not depend on a real cluster: K8s goes through the `app/core/k8s` abstraction, dev/test use FakeOrchestrator.

## Production release flow (deploy/app/k8s)

The single entry point is `SUPERDL_IMAGE_PREFIX=harbor.<domain>/superdl scripts/release.sh <tag>`; never bypass the script by editing manifest tags by hand:
admission policy assertion → migration Job → kustomize render, replace the `CHANGE_IMAGE_PREFIX` placeholder, pin platform images to **immutable digests**, then apply → rollout status → GET `/readyz` through the gateway from outside the cluster; any failing step exits non-zero.

Before the first release, copy `app/secrets.example.yaml` outside the repository and fill in real values, or feed the same fields through External Secrets / SealedSecrets; never apply the example directly or commit credentials.
A private Harbor project also needs `superdl-registry-pull` pre-created in the `superdl` namespace: type `kubernetes.io/dockerconfigjson`, key `.dockerconfigjson`, content from the Docker login config of a robot account with Pull + List Repository permissions only. Create it from a local config file with restricted permissions; do not put the robot secret on the command line.
The GitHub repository's `production` environment must have required reviewers; `HARBOR_HOST` / `HARBOR_ROBOT_NAME` / `HARBOR_ROBOT_SECRET` live in that environment, and the release job in `.github/workflows/release.yml` uses it as the approval gate.
Before releasing, set `FORWARDED_ALLOW_IPS` in `app/k8s/00-namespace-config.yaml` to the real network ranges (never `*`), replace the admin allow-list placeholder range in `app/k8s/04-gateway.yaml` with the office / bastion egress CIDRs, and replace the database and other endpoint CIDRs in `app/k8s/09-networkpolicy.yaml`; update them whenever an endpoint IP moves.

1. `helmfile -e <full|light> apply` (in cluster/, after `./preflight.sh`; the two tiers are in `cluster/README.md`) → create the per-domain Secrets from `app/secrets.example.yaml` (`superdl-db` (application role) / `superdl-db-migrate` (database owner, migration Job only) / `superdl-auth` / `superdl-crypto` / `superdl-metrics` / `superdl-edge` / `superdl-cloud` / `superdl-payment` / `superdl-registry` / `superdl-pg-backup`) and `superdl-registry-pull` (Harbor pull robot; optional when the project is public); with a self-signed / private database CA also create the ConfigMap `superdl-db-ca` (key `ca.crt`, mounted by every Deployment as an optional volume at `/etc/superdl/db-ca`). The field lists are `app/k8s/00-namespace-config.yaml` (non-secret) and `app/secrets.example.yaml` (secret); the prod-required set is the `_validate_prod` list in `docs/reference/security.md`
2. Tag: `gh release create vX.Y.Z --generate-notes` (no CHANGELOG is maintained). The tag triggers `.github/workflows/release.yml`: CI gates re-run → the api/web/admin images are built + Trivy-scanned + pushed to Harbor (repository secrets `HARBOR_HOST` / `HARBOR_ROBOT_NAME` / `HARBOR_ROBOT_SECRET`, variable `HARBOR_PROJECT` defaults to superdl). The api image is the same artifact for all three environments; the mock payment webhook route is registered only outside prod
3. `SUPERDL_IMAGE_PREFIX=harbor.<domain>/superdl scripts/release.sh vX.Y.Z` (prerequisites: `kubectl` + one of `crane` / `skopeo` / `docker buildx`, with read access to Harbor and a completed `docker login`; without any of the three the release is refused):
   - step 0 asserts that the Policy and Binding of all seven admission policies exist and that `validationActions` contains Deny;
   - step 1 checks `superdl-registry-pull` (warning only, not blocking);
   - step 2 creates the migration Job (`k8s/10-migrate-job.yaml`, a separate create) and `wait complete`s it before the rollout; `/readyz` compares the DB `alembic_version` with the code head and answers 503 `schema_mismatch` on mismatch and 503 `never_migrated` when never migrated;
   - step 3 renders with `kubectl kustomize` and applies: the tag is resolved to a digest and the three platform images are rewritten whole to `<prefix>/superdl-<name>@sha256:...`; the rendered output is self-checked for zero `CHANGE_*` placeholders and for **every** image in the manifests (third-party postgres / aws-cli included) carrying `@sha256:`; any miss refuses the rollout (`CHANGE_TAG` is reserved for the migration Job name);
   - step 4 waits for every Deployment (api + the 5 worker components + web/admin) to finish rolling (the readinessProbe is `/readyz`);
   - step 5 runs `curl -fsS https://<api-domain>/readyz` through the gateway from outside the cluster: the domain comes from the environment variable `SUPERDL_API_BASE_URL`, falling back to `SUPERDL_PUBLIC_BASE_URL` in the ConfigMap `superdl-api-config`; when neither is available or still a placeholder the step is skipped with a notice. On failure check the migration Job and the gateway chain first, fix and release again; there is no rollback.
4. First administrator (after the migration, once at the first release only): `cd apps/api && uv run python scripts/bootstrap_admin.py` (`seed_dev.py` is dev/test only); the password is printed once and the first login enforces TOTP enrolment
5. Backups: `06-pg-backup.yaml` takes a daily logical backup (the self-hosted single-instance PG form uses `pg/backup.sh` + `pg/wal-sync.sh`; the dump / basebackup / WAL chains are all gpg-encrypted and each exports a textfile metric); the k3s cluster state (token / cred / tls / datastore) is encrypted to the same mirror host every 6 hours by `cluster/k3s/state-backup.sh`; the mirror host must not be a node that carries tenant workloads; the restore drill is in `cluster/runbooks/pg-backup-restore.md`

### Release and migration conventions

- **Stop-the-world releases**: migrations and code share one tag, the order is always "`alembic upgrade head` first, code replacement second"; `/readyz` accepts only DB == code head, so old Pods answer 503 and leave the load balancer between migration completion and rollout completion.
- **No release rollback**: fix forward; the baseline migration's downgrade always raises.
- Migrations need no forward compatibility and destructive DDL is allowed (the commit message states the data impact); the migration Job carries `PGOPTIONS` (`lock_timeout=3s` / `statement_timeout=60s`), large-table changes beyond that budget run by hand in a maintenance window.

Hard go-live checks (must pass at every first release and after any change to the release channel):

- [ ] Deployment identity set in `00-namespace-config.yaml`: `SUPERDL_COMPLIANCE_PROFILE` (`none`|`cn`), `SUPERDL_PLATFORM_CURRENCY`, `SUPERDL_BILLING_TIMEZONE`. Existing mainland-China deployments upgrading to this revision must set `cn` / `CNY` / `Asia/Shanghai`: the migration locks `billing_identity` to CNY / Asia/Shanghai when money rows exist, and API / worker refuse to start until env matches (`docs/reference/platform-config.md`).
- [ ] `curl -s https://<api-domain>/api/v1/webhooks/mock -X POST` returns 404
- [ ] `curl -s https://<api-domain>/api/admin/v1/auth/login -X POST` returns 404
- [ ] `curl -s https://<api-domain>/metrics` returns 404 or 401
- [ ] One end-to-end test of an Alertmanager critical alert (admin alert stream and the on-call mailbox reaching a person; also verify any enabled Slack / PagerDuty / DingTalk receiver or `oncall_phone`)

## Production database requirements (read first)

The manifests in `app/k8s/` contain no PostgreSQL objects. Choose one of three production databases:

1. **Managed PG** (cloud RDS / self-run primary-replica on bare metal): PG ≥ 18, automatic backups + PITR (WAL archiving) on; `SUPERDL_DATABASE_URL` injected through `secrets.example.yaml`.
2. **CloudNativePG cluster** (the only supported form inside K8s): 3 instances + `backup` to object storage (continuous WAL archiving) + scheduled backup verification; a single-instance cnpg is not allowed in production.
3. **Self-hosted single-instance PG (Docker on the control-plane host)**: the form for small clusters without managed PG; files and the backup / PITR chain are in `pg/README.md`; the host is the database's single point of failure and the RPO is covered by the daily dump (24 h) + off-host WAL sync (5 minutes).

All three forms split two database roles: owner (runs migrations, Secret `superdl-db-migrate`) and application role (api / worker, Secret `superdl-db`, no DDL, `balance_ledger` append-only, `audit_log` immutable; created by `pg/roles.sql`).

Hard requirements (backup layers in `cluster/runbooks/pg-backup-restore.md`):

- **WAL archiving must be on**; daily `pg_dump` RPO = 24 h.
- **A restore drill per quarter following the runbook** (including a ledger chain spot check).

Connection alignment (re-check when changing replica counts or `SUPERDL_DB_POOL_SIZE`):

```
max_connections ≥ processes × (db_pool_size + max_overflow) + migration/ops reserve
              = (api 2 + worker 2+2+1+1+1) × (10 + 10) + 20 = 200
```

- The SQLAlchemy async engine defaults to `max_overflow=10`: the per-process peak is pool_size **+10**;
- the five worker Deployments each build their own pool, replica counts per `app/k8s/03-worker.yaml`;
- PG defaults to `max_connections=100`; production takes the formula value with headroom;
- api/worker processes carry `statement_timeout=30s / lock_timeout=5s / idle_in_transaction_session_timeout=60s`.

## Frontend availability and the HPA decision

web/admin frontends: 2 replicas each + PDB `minAvailable: 1` + liveness/readiness both probing `/` (see `app/k8s/07-frontends.yaml`).
**No HPA is configured**; both frontends run `requests == limits` (Guaranteed QoS); re-evaluate if SSR/BFF is introduced.

## Separate environment copies (staging / demo)

The repository defines only the full/light tiers, no staging overlay. Build your own: copy `cluster/environments/full.yaml` under a new name and let the overlay drop replicas to 1, change domains and turn off the second SMTP channel; on the `app/k8s/` side use a kustomize overlay or separate secrets + ConfigMap.
`SUPERDL_ENVIRONMENT` accepts only `dev` / `test` / `prod`; staging **still runs as `prod`**, only its secrets / ConfigMap / domains are separate; its PG is subject to the backup requirements above as well.

## Admin console access boundary

- The admin API is unreachable under the public api domain (in prod any Host other than the admin domain answers 404, always on, no switch, see `docs/reference/security.md`); `admin.superdl.example.com` itself is only TLS + admin JWT + TOTP (enforced for every role).
- Production must add a network boundary on top: `SecurityPolicy superdl-admin-allowlist` in `app/k8s/04-gateway.yaml` (attached to the `superdl-admin` HTTPRoute) enables a source IP allow-list **by default**: `authorization.defaultAction: Deny` plus one `action: Allow` rule with `principal.clientCIDRs` holding the office / bastion egress CIDRs (several rules for several ranges). A VPN or an identity-aware proxy (oauth2-proxy etc.) can replace it.
- The placeholder is `192.0.2.0/24`; `preflight.sh` scans for that range and refuses to pass while it is unreplaced.
- `defaultAction: Deny` must not be omitted.
- Source IP authenticity depends on `envoyService.externalTrafficPolicy: Local` in the `EnvoyProxy`; never change it to `Cluster`.
- Emergency path: when the allow-list locks you out use `kubectl port-forward`; do not open 0.0.0.0/0.
- Grafana and other management surfaces stay on the internal network or port-forward; do not expose them through the gateway.
