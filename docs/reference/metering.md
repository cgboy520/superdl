# Usage and metrics

Prometheus proxy queries, `usage_hourly` aggregation, and reconciliation of event billing against metric estimates.

## Data model

- `usage_hourly`: instance_id, hour_start, gpu_util_avg, UNIQUE(instance_id, hour_start)

## Contract

| Endpoint                                                 | Role / auth      | Notes                                                                                                         |
| -------------------------------------------------------- | ---------------- | ------------------------------------------------------------------------------------------------------------- |
| `GET /api/v1/instances/{uuid}/metrics?range=1h\|6h\|24h` | user             | Proxied PromQL filtered by the tenant namespace; Prometheus unavailable → 503                                 |
| `GET /api/v1/metrics/instances`                          | user             | Sparse 1 h gpu_util series for the caller's running instances (cap 20); source down → 200 `{available:false}` |
| `GET /api/admin/v1/reconciliation?day=`                  | finance/readonly | Event billing total vs metric estimate total + diff % + list of divergent instances                           |
| `GET /api/admin/v1/reconciliation/export?day=&lang=`     | finance/readonly | The same report as CSV                                                                                        |

## Rules and invariants

- Metrics are for display and reconciliation only and never enter billing; billing is unaffected when Prometheus is down.
- Tenants can only query their own instances' metrics: the namespace is injected server-side; arbitrary PromQL is never accepted.
- Batch endpoint paths must avoid the `/instances/*` prefix or the `{uuid}` route swallows them.
- `usage_hourly` aggregation is idempotent (UNIQUE + ON CONFLICT); a failed query only skips that instance-hour; gaps are logged as `usage_aggregation_partial` and never back-filled automatically. Instances with a reconciliation diff > 2 % enter the divergence list.
- Malformed Prometheus responses are normalised to `PrometheusUnavailable`: the detail endpoint answers 503, batch / node endpoints answer `available=false`; nothing turns into a 500.
- The metric source follows the instance spec's `pool_label`: the hami pool uses HAMi vGPUmonitor per-container metrics and falls back to DCGM when empty; kata / mig pools always use DCGM. The criterion is the pool, not the tier.
- Label constants are at the top of `prom.py`: `DCGM_NODE_LABEL` is dcgm-exporter 4.x's lowercase `hostname`. `NODE_QUERIES` group by that dimension into per-GPU series, through the same `query_range_multi` as `HAMI_QUERIES`.
- A `prometheus_url` pointing at localhost is only a boot WARNING in prod, see [security.md](./security.md).
- Node-level metric endpoints are in [observability.md](./observability.md).
