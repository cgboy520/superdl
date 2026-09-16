# Logs and audit retention (Loki)

Components: the helmfile releases `loki` (grafana-community/loki, single-replica Monolithic) + `alloy` (grafana/alloy).
Collection surface: container logs of every namespace (discovery.kubernetes) + the apiserver audit file on control-plane nodes (`/var/lib/rancher/{rke2,k3s}/server/logs/audit.log`).

Multi-tenancy (`auth_enabled: true`): Alloy assigns the tenant by namespace, platform components and the apiserver audit go to `platform`, `tenant-*` workloads to `tenant`; ingestion limits count per tenant. Two `stage.replace` steps run before ingestion: the value after `token=` in log lines, and the `value` of `"name":"JUPYTER_TOKEN"` / `"name":"AUTHORIZED_KEYS"` in Pod templates inside apiserver audit lines, are both replaced with `<redacted>`. The tenant header is self-declared; the network boundary is `../monitoring-netpol.yaml` (only alloy/grafana/prometheus can reach loki:3100).

RBAC: Alloy's ClusterRole has only read on pods / pods/log / namespaces / services / endpoints / endpointslices / nodes (`rbac.rules` / `rbac.clusterRules` in `values/alloy.yaml`), no secrets / configmaps; Loki turns the ruler sidecar off (`sidecar.rules.enabled: false`) and neither the SA nor the Pod mounts a token. Both are guarded by the helm render assertion in the CI `monitoring-rbac` job and by `../preflight.sh`.

## Retention (compliance baseline)

- **Loki `retention_period: 4320h` (180 days)**, `values/loki.yaml` (compactor `retention_enabled`); disk 50Gi CephFS on the full tier, 10Gi TopoLVM on light.
- DB `audit_log` table: 365 days (`SUPERDL_AUDIT_RETENTION_DAYS`), the primary source of structured audit; Loki is the second retention path for request chains / exceptions / apiserver audit.

## Querying

Grafana (full tier) → Explore → the `Loki (platform)` / `Loki (tenant)` data sources (kps.yaml additionalDataSources); or `logcli` with `--org-id`: after `kubectl -n monitoring port-forward svc/loki 3100:3100` run `logcli --addr=http://localhost:3100 --org-id=platform query ...` (the queries below use the platform tenant; for tenant instance logs use `--org-id=tenant`).

The six queries below are, in order: full chain by request ID, API unhandled exceptions, outbox dead letters / retries, audit events, apiserver delete operations, apiserver operations by platform ServiceAccounts.

```logql
{namespace="superdl"} |~ `"request_id":"<request-id>"`

{namespace="superdl", container="api"} |~ "unhandled_exception"

{namespace="superdl", container="worker"} |~ "outbox_task_(dead|failed)"

{namespace="superdl", container="api"} |~ "audit"

{job="kube-apiserver-audit"} |~ `"verb":"delete"`
{job="kube-apiserver-audit"} |~ `"user":{"username":"system:serviceaccount:superdl`
```

Platform logs in prod are JSON lines (structlog; `request_id` / `level` / `event` are keys); filter by key after `| json`, e.g. `{namespace="superdl"} | json | level="error"`.

## Alerting

Unhandled exceptions, outbox dead letters and the like already alert through Prometheus metrics (the `superdl.platform` rule group in the kps values: `ApiHighErrorRate` / `OutboxTaskDead` etc.). To alert on log content, build `count_over_time(...) > 0` rules in the Loki ruler over the API exception and outbox dead-letter / retry queries above and route them to the same Alertmanager.

## Collection self-check

Alloy should run one Pod per node, control-plane nodes included; when the apiserver audit stream is missing, first confirm the Alloy Pod on the server is ready, then check the tolerations and the audit file mount in `values/alloy.yaml`.

```bash
kubectl -n monitoring get pods -l app.kubernetes.io/name=alloy
kubectl -n monitoring logs deploy/loki --tail=5
```
