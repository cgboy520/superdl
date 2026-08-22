# 可观测性与运维

业务指标、request-id、健康探针、告警规则与管理端自绘监控。

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /metrics` | Bearer `SUPERDL_METRICS_TOKEN` | Prometheus 抓取 |
| `GET /readyz` | 匿名 | 探 DB |
| `GET /api/admin/v1/nodes/{node_name}/metrics?range=1h\|6h\|24h` | ops/readonly | `{available, gpus:[{index, util:[[ts,v]], mem_used_mb, temp}], xid_count_24h}`;断源 `available=false` 且返 200 |
| `GET /api/admin/v1/alerts` | ops/finance/readonly | 告警流,条目关联节点/实例内部链接 |
| platform-config `observability` 组 | admin | 键 `grafana_url`(str,`https?://` pattern,可空) |

业务指标在 `app/core/metrics.py`:死信、结算失败、泄漏 Pod、回调金额不符、查单收敛、HTTP 直方图(用完整路由模板)。

## 规则与不变量

- request-id 贯穿全链路(contextvars + 响应头);未捕获异常统一 500 错误体,Sentry 为可选依赖 seam。
- worker 自起 `/metrics` 端口(默认 9000,`SUPERDL_WORKER_METRICS_PORT`):结算失败、死信、泄漏回收等指标产生在 worker 进程内。
- 抓取配置 `deploy/app/k8s/08-monitoring.yaml` = API ServiceMonitor(Bearer)+ worker PodMonitor。
- WorkerDown 告警按心跳 Gauge 判定,不用 `absent()`:带 label 的 Counter 在首次 inc 前没有序列,`absent()` 会常驻误报。
- worker 支持 SIGTERM 优雅停机并写心跳文件(K8s exec 探针据此判活)。
- worker 心跳由独立协程触碰,不挂在 outbox 循环上(长任务会把自己探活探死)。
- Alertmanager critical 走双通道(webhook + 外部 SMTP),第二条不经过平台自身组件。
- 每日数据保洁:验证码、refresh 记录、已完成 outbox、超保留期审计。
- 管理端监控自绘、不做 Grafana iframe:节点页每卡热力格(util%/显存/温度染色,XID>0 红点)+ 节点详情 ECharts 曲线;`grafana_url` 仅作外链,未配置时只显示一行提示。
- 指标断源时管理端降级为「已租/空闲」形态,不报错、不开天窗。
- 抓取 HAMi 需在 kps values 加 additionalScrapeConfigs(scheduler + vGPUmonitor)并配 `absent(up{job="hami-scheduler"})` 告警。
- light 档用同一 kube-prometheus-stack 的精简 values(grafana off / retention 3d / 资源收紧),由 helmfile environments 选用。
- 备份 Job 用 initContainer pg_dump + 官方 aws-cli 镜像,运行期不装包;备份失败与超期有告警。
- 指标源与用户端指标端点见 [metering.md](./metering.md)。
