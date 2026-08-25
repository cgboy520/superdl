# Runbook 索引

值班处置手册。每份 runbook 的结构:触发条件 → 处置步骤(带判据)→ 验收 / 演练。

| 文件 | 场景 |
|---|---|
| [gpu-fault-sop.md](./gpu-fault-sop.md) | GPU Xid 致命错误:隔离 → 停机结算 → 通知 → 补偿 → 回归 |
| [pg-backup-restore.md](./pg-backup-restore.md) | 资金库备份分层、逻辑备份恢复、季度演练与 RTO 记录 |
| [image-prewarm.md](./image-prewarm.md) | 托管镜像仓迁移、平台镜像发布、Spegel P2P 与预热 |
| [acme-dns.md](./acme-dns.md) | 泛域名证书 DNS01(acme-dns)部署、凭据轮换与回滚 |
| [loki-logging.md](./loki-logging.md) | 日志留存口径、LogQL 排障查询、采集自检 |
| [cluster-validation.md](./cluster-validation.md) | CI 覆盖不到的实机验证清单与每次上线的发布检查单 |

发布与回滚在 [`../../README.md`](../../README.md);集群装机与 token 轮换在 [`../README.md`](../README.md)。

## 告警 → 第一步

告警规则在 `deploy/cluster/values/kps.yaml`(`superdl.platform` 与 GPU 规则组),critical 走 webhook + 外部 SMTP 双通道。
下表按告警名给出第一动作与对应文档;平台指标含义见 `docs/reference/observability.md`。

| 告警 | 含义 | 第一步 | 文档 |
|---|---|---|---|
| GPUXidCriticalError | GPU 硬件致命错误 | `kubectl cordon <node>`,按 SOP 走 | [gpu-fault-sop.md](./gpu-fault-sop.md) |
| GPUHighTemperature | GPU >85°C 持续 5 分钟 | 查机房散热;持续则 cordon 观察 | [gpu-fault-sop.md](./gpu-fault-sop.md) |
| NodeGPUUnavailable | GPU 节点 NotReady | 查节点;运行中实例由 reconciler 判 node_lost 并停费 | `docs/reference/orchestrator.md`、`docs/reference/nodes.md` |
| SharedPoolUtilSaturated | 共享池持续打满 | 复核该 SKU 超卖参数与容量 | `docs/reference/catalog.md` |
| JuiceFSMountFailed | 数据盘挂载失败 | 查 CSI Pod 与 metaurl Secret | [cluster-validation.md](./cluster-validation.md) D 节 |
| HamiSchedulerDown | 共享池调度器指标缺失 | 查 `hami-scheduler` Pod;期间共享档下单报 CLUSTER_NOT_READY | [cluster-validation.md](./cluster-validation.md) C 节 |
| CertExpiringSoon / CertExpiringCritical / CertNotReady / CertManagerMetricsMissing | 泛域名证书续签链路异常 | `kubectl describe certificate`,查 DNS01 委托与 acme-dns 账户 | [acme-dns.md](./acme-dns.md) |
| OutboxTaskDead | 编排任务进死信 | 管理端总览死信卡:看原因后重放或忽略(需原因) | `docs/reference/orchestrator.md` |
| OutboxTaskTimeout | outbox 任务执行超时 | 查 worker 日志中卡住的任务类型 | [loki-logging.md](./loki-logging.md) 查询 3 |
| SettlementFailed / SettlementLagging | 结算失败 / 水位线落后 | 查 worker 日志失败实例;结算幂等可重跑 | `docs/reference/billing.md` |
| SettlementGapRecorded / SettlementGapUnresolved | 结算缺口登记 / 超 1h 未核销 | 管理端 财务 › 结算缺口:重放或人工核销 | `docs/reference/billing.md` |
| PaymentCallbackMismatch | 回调金额与订单不符 | 财务异常清单核对;疑似攻击时保留报文 | `docs/reference/payment.md` |
| PaymentClosedOrderRescued | 关单后回调自动入账 | 核对本地关单 TTL 与渠道过期是否同步 | `docs/reference/payment.md` |
| PaymentRecoverFailed | 查单收敛单笔入账失败 | 按 error 标签人工核对该笔 | `docs/reference/payment.md` |
| FundReconcileMismatch | 资金账实不平 | 先冻结出账(退款打款),再按 ledger id 定位断链 | `docs/reference/billing.md` |
| InstanceNodeLost | 实例因节点失联被判停 | 核对是否退费;节点恢复后用户可重开 | `docs/reference/orchestrator.md` |
| LeakedPodsReclaimed / ReconcileLeakAborted | 泄漏 Pod 批量回收 / 回收熔断 | 核对节点残留 Pod 与 DB 记录差异 | `docs/reference/orchestrator.md` |
| ReconcileStuckInstances | 实例悬挂超 15 分钟 | 管理端实例详情人工介入(强制停止 / 释放) | `docs/reference/orchestrator.md` |
| ApiHighErrorRate | API 5xx >5% 持续 5 分钟 | 按 request_id 查未捕获异常 | [loki-logging.md](./loki-logging.md) 查询 2 |
| WorkerDown | worker 心跳缺失 >2 分钟 | `kubectl -n superdl rollout status` 五个 worker Deployment | `../../README.md` 回滚指引 |
| PgBackupFailed / PgBackupStale | 每日备份失败 / 超 28h 无成功 | 查 CronJob 日志与 S3 凭据;RPO 正在拉长 | [pg-backup-restore.md](./pg-backup-restore.md) |
| AuditWriteFailed | 审计行写入失败 | 查 DB 与审计写路径;期间 fail-open 操作可能无留痕 | `docs/reference/security.md` |
| PatrolFailed | 余额巡检环节异常 | 查 worker 日志 stage 标签 | `docs/reference/billing.md` |
