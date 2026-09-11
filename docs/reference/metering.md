# 用量与指标

Prometheus 代理查询、`usage_hourly` 聚合、事件计费与指标估算对账。

## 数据模型

- `usage_hourly`:instance_id、hour_start、gpu_util_avg、UNIQUE(instance_id, hour_start)

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/instances/{uuid}/metrics?range=1h\|6h\|24h` | user | 代理 PromQL,按租户 namespace 过滤;Prometheus 不可用 → 503 |
| `GET /api/v1/metrics/instances` | user | 本人 running 实例(cap 20)近 1h gpu_util 稀疏序列;断源返 200 `{available:false}` |
| `GET /api/admin/v1/reconciliation?day=` | finance/readonly | 事件计费合计 vs 指标估算合计 + diff% + 差异实例清单 |
| `GET /api/admin/v1/reconciliation/export?day=&lang=` | finance/readonly | 同一报告的 CSV |

## 规则与不变量

- 指标只做展示与对账,不参与计费;Prometheus 停机时计费不受影响。
- 租户只能查自己实例的指标:namespace 由服务端注入,禁止接受任意 PromQL。
- 批量端点路径必须避开 `/instances/*` 前缀,否则被 `{uuid}` 路由吞掉。
- `usage_hourly` 聚合幂等(UNIQUE + ON CONFLICT);单实例查询失败只跳过该实例该小时;缺口实例记 `usage_aggregation_partial` 日志,不自动回填。对账 diff >2% 的实例进差异清单。
- Prometheus 响应形态异常统一归 `PrometheusUnavailable`:详情端点 503,批量/节点端点 `available=false`,不击穿成 500。
- 指标源按实例 spec 的 `pool_label` 选:hami 池用 HAMi vGPUmonitor per-container 指标,查空回落 DCGM;kata / mig 池恒用 DCGM。判据是池不是档位。
- 标签常量集中在 `prom.py` 头部:`DCGM_NODE_LABEL` 是 dcgm-exporter 4.x 的小写 `hostname`。`NODE_QUERIES` 走该维度出 per-GPU 多序列,与 `HAMI_QUERIES` 同经 `query_range_multi`。
- `prometheus_url` 指向本地时 prod 启动只打 WARNING,见 [security.md](./security.md)。
- 节点级指标端点见 [observability.md](./observability.md)。
