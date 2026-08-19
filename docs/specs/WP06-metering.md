# WP6 · 用量

> ✅ 已交付(tests/test_metering.py;Prometheus 停机计费不受影响已验证)。

## 目标
Prometheus 代理查询 + usage_hourly 聚合 + 事件计费 vs 指标估算对账。仅展示与对账,不参与计费。

## 数据
- `usage_hourly`:instance_id、hour_start、gpu_util_avg、gpu_util_p95、vram_max_mb、cpu_avg、UNIQUE(instance_id, hour_start)

## API
- `GET /instances/{id}/metrics?range=1h|6h|24h` → 代理 PromQL(DCGM_FI_DEV_GPU_UTIL 等),按租户 namespace 过滤;Prometheus 不可用 → 503 + MONITORING_UNAVAILABLE(前端显示"监控暂不可用,不影响计费")
- 管理:`GET /api/admin/v1/reconciliation?day=` → 事件计费合计 vs 指标估算合计 + diff% + 差异实例清单

## 验收
- Prometheus 停机:计费不受影响,metrics 接口优雅降级
- usage_hourly 聚合幂等(UNIQUE);对账 diff>2% 的实例被列出
- 租户只能查自己实例的指标(namespace 注入,禁止任意 PromQL)
