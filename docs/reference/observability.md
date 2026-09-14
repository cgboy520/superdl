# 可观测性与运维

业务指标、request-id、健康探针、告警规则与管理端自绘监控。

## 契约

| 端点                                                            | 角色/鉴权                      | 说明                                                                                                                                                                                        |
| --------------------------------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /metrics`                                                  | Bearer `SUPERDL_METRICS_TOKEN` | Prometheus 抓取                                                                                                                                                                             |
| `GET /healthz`                                                  | 匿名                           | liveness:进程活着即 200,不探依赖;不进 openapi                                                                                                                                               |
| `GET /readyz`                                                   | 匿名                           | readiness:探 DB 并比对 `alembic_version` 与代码 head,**必须完全一致**;落后/领先/未知(503 `schema_mismatch`)、从未迁移(503 `never_migrated`)、多 head(503 `multi_head`)不接流量;不进 openapi |
| `GET /api/admin/v1/nodes/{node_name}/metrics?range=1h\|6h\|24h` | ops/readonly                   | `{available, gpus:[{index, util:[[ts,v]], mem_used_mb, temp}], xid_count_24h}`;断源 `available=false` 且 200                                                                                |
| `GET /api/admin/v1/alerts`                                      | ops/finance/readonly           | 告警流                                                                                                                                                                                      |
| platform-config `observability` 组                              | admin                          | `grafana_url`(`https?://`,可空)、`oncall_phone`(critical 平台告警额外经 outbox `notify.sms` 直发,留空不启用)                                                                                |

业务指标在 `app/core/metrics.py`:死信、任务超时、结算失败与落后、未核销结算缺口(`superdl_settlement_gap_unresolved`,DB 口径 gauge,>0 持续 15 分钟告警)、资金账实差异、巡检分阶段失败、泄漏 Pod 与熔断、悬挂实例、节点失联、回调金额不符、关单后入账、渠道反向通知与人工处置(`superdl_payment_reversal_resolved_total{action}`)、查单单笔失败、数据盘 PVC 下发死信、审计写失败、worker 心跳、HTTP 直方图(完整路由模板)、竞价抢占计数、SSH 端口池水位(`superdl_ssh_port_pool_ports{state}`,reconciler 每轮)、负余额敞口(`superdl_wallet_negative_count` / `superdl_wallet_negative_sum_yuan`,余额巡检每轮)、平台配置写入(`superdl_platform_config_write_total{domain}`)。

安全域计数:`superdl_login_failed_total{actor_type}`(user / admin)、`superdl_authz_denied_total{actor_type}`(已认证但被角色门拒的 403)、`superdl_admin_privilege_change_total`(建号 / 改角色 / 停用 / 重置口令 / 重置 MFA)、`superdl_pii_reveal_rows_total{kind}`(kind `tenant_realname` / `invoice_identity`)、`superdl_endpoint_auth_denied_total`(服务端点网关鉴权回调拒绝)。滥用域计数:`superdl_user_signup_total`、`superdl_sms_sent_total{purpose}`;租户出向流量取 cAdvisor `container_network_transmit_bytes_total`。

## 规则与不变量

- **故障类指标都有对应告警规则**(`deploy/cluster/values/kps.yaml`),没有消费方的指标不保留。唯一不配告警的是 `superdl_spot_preempted_total`(消费方是运维侧 PromQL);该计数在事务内自增、回滚不退回,是**上界**;对账以 `instance_events` 里 `reason='preempted'` 为准。
- 安全域告警在 kps 的 `superdl-security` 规则组(`superdl.security`):`LoginFailureSpike`(warning,按 actor_type,5 分钟失败 > 50)、`AdminLoginFailureSpike`(critical,10 分钟失败 > 10)、`AuthzDenialSustained`(warning,10 分钟速率 > 0.5/s 持续 15 分钟)、`AdminPrivilegeChanged`(critical,5 分钟 > 0)、`PiiRevealVolumeHigh`(warning,按 kind,1 小时 > 200 行)、`PiiRevealBurst`(critical,5 分钟 > 500 行)、`ApiUnauthorizedRateHigh`(warning,401/403 占比 > 30% 持续 10 分钟)、`PaymentConfigWritten`(critical,payment / crypto 配置组任一写入)、`EndpointAuthDenialSustained`(warning,> 1/s 持续 15 分钟)。runbook 指向 Loki 查询(`deploy/cluster/runbooks/loki-logging.md`),排查入口按 `request_id` 串全链。
- 滥用域告警在 `superdl-abuse` 规则组(`superdl.abuse`):`SignupVelocityHigh`(1 小时注册 > 100)、`SmsBurst`(10 分钟按 purpose > 300 条)、`ApiRateLimitedHigh`(应用层 429 > 5/s 持续 10 分钟;边缘 Envoy 的 429 不在此口径)、`TenantEgressHigh`(单租户 ns 出向 > 40 MB/s 持续 15 分钟)。平台组另有 `SshPortPoolLow` / `SshPortPoolExhausted`、`WalletNegativeExposure`(负余额敞口 > 1000 元持续 30 分钟)、`PaymentReversalReleased`(critical,条条)。
- `superdl_authz_denied_total` 只在 `adminapi/deps.require_roles`(及共用它的 PII 明文闸)计数;用户端 403 不计。
- request-id 贯穿全链路(contextvars + 响应头);未捕获异常统一 500 错误体。异常告警经日志栈(Loki 查询见 `deploy/cluster/runbooks/loki-logging.md`)。
- 日志:structlog + stdlib 桥接(ProcessorFormatter);prod=JSON、dev/test=Console;级别 `SUPERDL_LOG_LEVEL`(默认 INFO);outbox payload 带 `_request_id`,worker 执行时回填日志上下文。
- **异常栈不带局部变量**:prod 结构化栈帧渲染显式关 `show_locals`;dev/test 的 Console 渲染钉纯文本栈(`plain_traceback`),不让 structlog 自动切到 rich 的带局部变量渲染。
- worker 自起 `/metrics` 端口(默认 9000,`SUPERDL_WORKER_METRICS_PORT`,同 `SUPERDL_METRICS_TOKEN` Bearer)。抓取配置 `deploy/app/k8s/08-monitoring.yaml` = API ServiceMonitor(按 Service 标签 `app: superdl-api` 选中,Service 必须带该标签)+ worker PodMonitor(kps 须 `podMonitorSelectorNilUsesHelmValues: false`,否则只选带 `release` 标签的 PodMonitor);两者均带 Bearer。上线后用 Prometheus `up{namespace="superdl"}` 核对两个抓取池都在。
- 定时任务单轮超过周期 80% 时 worker 打 warning(`scheduled_tick_slow`)。
- WorkerDown 告警按心跳 Gauge 判定,不用 `absent()`。
- worker 支持 SIGTERM 优雅停机并写心跳文件(K8s exec 探针据此判活);心跳由独立协程触碰。
- Alertmanager critical 必走双通道(平台 webhook + 外部 SMTP);可选第三通道钉钉群机器人(经 sidecar 转换器,默认未启用,见 `deploy/cluster/values/kps.yaml`);另可配 `oncall_phone` 直发值班短信。
- 每日数据保洁:验证码、refresh 记录、已完成 outbox、超保留期审计。保留期见 [limits.md](./limits.md)。
- 管理端监控自绘、不做 Grafana iframe:节点页每卡热力格(util%/显存/温度,XID>0 红点)+ 节点详情 ECharts 曲线;`grafana_url` 仅作外链。指标断源时管理端降级为「已租/空闲」形态。
- 抓取 HAMi 需在 kps values 加 additionalScrapeConfigs(scheduler + vGPUmonitor)并配 `absent(up{job="hami-scheduler"})` 告警。
- 集群维查询模板在 `apps/api/app/modules/metering/prom.py` 的 `COMPONENT_QUERIES`(DCGM 样本新鲜度 / 抓取目标 up 比 / 触发中告警数),经 `metering/service.py` 的 `cluster_component_metrics()` 供节点巡检取用,并入组件体检快照。Prometheus 未配或查询失败返回空:对应事实留空,不改任何组件的状态位。
- light 档用同一 kube-prometheus-stack 的精简 values(grafana off / retention 3d / 资源收紧),由 helmfile environments 选用。
- 备份 Job 用 initContainer pg_dump + 官方 aws-cli 镜像;备份失败与超期有告警。
- 指标源与用户端指标端点见 [metering.md](./metering.md)。
