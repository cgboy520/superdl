# 用量与指标

Prometheus 代理查询、`usage_hourly` 聚合、事件计费与指标估算对账。

## 数据模型

- `usage_hourly`:instance_id、hour_start、gpu_util_avg、gpu_util_p95、vram_max_mb、cpu_avg_pct、UNIQUE(instance_id, hour_start)

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/instances/{uuid}/metrics?range=1h\|6h\|24h` | user | 代理 PromQL,按租户 namespace 过滤;Prometheus 不可用 → 503 |
| `GET /api/v1/metrics/instances` | user | 本人 running 实例(cap 20)近 1h gpu_util 稀疏序列;断源返 200 `{available:false}` |
| `GET /api/admin/v1/reconciliation?day=` | finance/readonly | 事件计费合计 vs 指标估算合计 + diff% + 差异实例清单 |

## 规则与不变量

- 指标只做展示与对账,不参与计费;Prometheus 停机时计费不受影响,指标接口优雅降级。
- 租户只能查自己实例的指标:namespace 由服务端注入,禁止接受任意 PromQL。
- 批量端点路径必须避开 `/instances/*` 前缀,否则会被 `{uuid}` 路由吞掉。
- `usage_hourly` 聚合幂等(UNIQUE + ON CONFLICT);对账 diff >2% 的实例进入差异清单。
- 指标源按实例 spec 的 tier 选:shared 档用 HAMi vGPUmonitor per-container 指标,查空回落 DCGM;dedicated/mig 恒用 DCGM。HAMi 共享卡上 DCGM 的 `{pod=}` 归属不可靠,不得用于 shared 档。
- `prom.py` 含 `NODE_QUERIES`(DCGM Hostname 维度 per-GPU 多序列)与 `HAMI_QUERIES`,`query_range_multi` 返回多序列;标签常量集中在该文件头部。
- `prometheus_url` 在 prod 下 fail-fast(含 localhost 拒启)。
- 节点级指标端点见 [observability.md](./observability.md)。
