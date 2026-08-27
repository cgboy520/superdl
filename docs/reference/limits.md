# 配额、限流与保留期

平台对用户与运维施加的全部数字型限制,按「哪里能改」分层。数值以代码为准,这里给出承载它的位置;
改默认值时同步本页。三层配置链:用户级覆盖(`user_quota_overrides`)→ 策略参数(`policy_overrides`,管理端在线改)
→ env 默认(`SUPERDL_*`,`app/core/config.py`);未标注的即代码常量。

## 每用户配额

| 项 | 默认 | 可调范围 | 承载 |
|---|---|---|---|
| 实例数 | 10 | 1~1000 | 用户覆盖 → 策略 `max_instances_per_user` → env |
| GPU 总数 | 8 | 1~1024 | 同上 `max_gpus_per_user`(只计 GPU 实例;CPU 实例 gpu_count=0,不计入这一维) |
| CPU 实例 vCPU 总数 | 64 | 1~4096 | 策略 `max_vcpus_per_user`(无用户级覆盖列;只计 `gpu_count=0` 的实例,GPU 实例不计入)。超限报 `VCPU_QUOTA_EXCEEDED` |
| 数据盘数 | 20 | 1~1000 | 同上 `max_disks_per_user` |
| 单盘容量 | 10~4096 GB | 下限 1~1024,上限 10~65536 | 策略 `disk_min_gb` / `disk_max_gb` |
| 单实例 GPU 数 | 按 SKU `max_gpus_per_instance`(UI 给 1/2/4/8;CPU 规格为 0,只收 `gpu_count=0`) | — | `skus` |
| 单个 GPU 节点让给 CPU 实例的 vCPU | 16 | 0~1024 | 策略 `gpu_node_cpu_instance_vcpu_cap`;0 = 不许 CPU 实例落 GPU 节点(pool≠cpu 的 CPU 规格一律判无容量)。近似库存口径,见 [catalog.md](./catalog.md) |
| 进行中工单 | 10 | — | `tickets/service.py` `MAX_OPEN_TICKETS` |
| SSH 公钥 | 不限;同用户指纹唯一 | — | `ssh_keys` |
| 容器临时存储 | 请求 2Gi,上限 64Gi | — | `core/k8s/real.py` |

集群级:SSH NodePort 端口池 30000~32767(排除 30500),即单集群最多约 2767 台带 SSH 的实例(`SUPERDL_SSH_PORT_RANGE_START` / `SUPERDL_SSH_PORT_RANGE_END`,排除集 `SUPERDL_SSH_PORT_EXCLUDED` 默认 `{30500}`;运行期撞占的端口标 blocked 并周期复检放回,水位见 `GET /api/admin/v1/nodes/port-pool`)。

## 计费与回收时钟

| 项 | 默认 | 承载 |
|---|---|---|
| 开户前余额须覆盖的小时数 | 1h | 策略 `afford_cover_hours`(1~24) |
| 低余额预警阈值 | 预估可用 <24h | 只存 `users.low_balance_warn_hours`(用户在 1~168h 内自设,默认 24);不设平台级策略键 |
| 欠费冻结到回收 | 72h | 策略 `freeze_grace_hours`(1~720) |
| 数据盘欠费宽限 / 冻结 | 7 天 / 30 天 | 策略 `disk_grace_days` / `disk_frozen_days`(各 1~365) |
| 数据盘单价 | 0.0350 元/GB·月 | 策略 `disk_price_gb_month`(0.0010~1.0000),建盘时快照 |
| failed 实例保留 | 7 天后回收 | env `failed_retention_days` |
| stopped 实例保留 | 30 天后回收,提前 7 天预警 | env `stopped_retention_days` / `stopped_retention_warn_days` |
| creating 超时 | 300s → failed 退款 | env `creating_timeout_seconds` |
| running 持续 not-ready 判失联 | 600s(须宽于 unreachable toleration 300s) | env `running_unready_timeout_seconds` |
| stopping / releasing 悬挂 | 各 600s,一档重发删除、二档强删 | env `stopping_timeout_seconds` / `releasing_timeout_seconds` |
| 泄漏回收熔断 | 未知 Pod 占比 >0.5 即中止本轮 | env `leak_reclaim_abort_ratio` |
| 小时结算追平上限 | 72h / 14 天,超出登记缺口 | `billing/settlement.py` |
| 单对象结算连续失败 | 3 轮进死信缺口 | 同上 `DEAD_LETTER_AFTER` |
| 充值单有效期 | 2h | env `recharge_order_ttl_seconds` |
| 查单 poller 扫描窗 | 60s ~ 48h 内的 pending / failed 单,每轮 50 条 | `billing/payment_service.py` |
| 创建类幂等键窗口 | 24h(实例 / 数据盘;窗外同键按新单) | `core/idempotency.py` `IDEMPOTENCY_WINDOW`(`find_replay` 供各创建入口共用) |
| 镜像预热覆盖率门槛 / 复检 | 90% / 24h | 策略 `prewarm_min_coverage_pct` / `prewarm_recheck_hours` |
| Jupyter 一次性票据 | 60s | env `jupyter_ticket_ttl_seconds` |
| 账号注销冷静期 | 7 天 | `account/service.py` |
| 节点注册令牌 | 默认 24h,1~168h | 创建时指定 |
| 装机无心跳判失败 | 2h | `nodes/reconciler.py` |
| 节点 Missing 后删行 | 7 天 | `nodes/patrol.py` `MISSING_RETENTION` |

## 会话与凭据

| 项 | 值 | 承载 |
|---|---|---|
| 用户 access / refresh token | 1h / 7d(prod 上限同此) | env `access_token_ttl_seconds` / `refresh_token_ttl_seconds` |
| refresh 重放宽限 | 同 jti 10s 内视为并发重试 | `account/service.py` |
| 管理端会话 | 续期宽限 15min,单次会话最长 12h | `adminapi/service.py` |
| TOTP 绑定票 / 二要素票 | 10min / 5min;恢复码 10 枚一次性 | 同上 |
| 管理员口令 | ≥12 字符,≤72 字节 | 同上 |
| 用户密码 | ≤72 字节(bcrypt 上限) | `account/schemas.py` |
| 短信验证码 | 有效 300s;失败 5 次作废;同号重发 60s 起 ×2 递增、封顶 480s | env `sms_code_ttl_seconds` 等,`account/service.py` |

## 应用层限流

固定窗口,计数落 PG(`rate_limit_counters`),多副本共享;429 带 `Retry-After`。边缘层(Envoy Gateway)对公网 API 域另有**每源 IP** 20 rps / 600 rpm 兜底(`deploy/app/k8s/04-gateway.yaml` 的 `BackendTrafficPolicy`,`sourceCIDR.type: Distinct` 才是每 IP 一个桶);管理面不配边缘限流,靠源 IP 白名单。

原 ingress-nginx 的**每源 IP 20 并发连接**这一条已不存在:Envoy Gateway 没有每源 IP 连接数原语,`ClientTrafficPolicy.connection.connectionLimit` 是每个 Envoy 实例的连接总量,现配 10000 只作防内存耗尽的兜底,不是 20 的等价值。取舍见 [security.md](./security.md)「限流分层」。

| 动作 | 维度 | 限额 | 备注 |
|---|---|---|---|
| 用户登录 | IP+手机号 / 账号 | 5 次/5min / 10 次/15min | 只计失败,成功清零 |
| 用户登录 | IP / 账号日窗 | 60 次/时 / 30 次/日 | 只计失败,不清零 |
| 注册、找回密码 | IP+手机号 | 各 5 次/5min | |
| 实名核验 | 用户 | 5 次/时 | |
| 发码(尝试) | IP | 20 次/时 | |
| 发码(消费) | 手机号 | 10 次/日 | 按验证码被消费计 |
| 发码(平台) | 全局 | 1000 次/时,5000 次/日 | `core/sms.py` |
| 管理端登录 | IP+账号 / 账号 | 5 次/5min / 10 次/15min | 只计失败,成功清零 |
| 管理端登录 | IP / 账号日窗 | 30 次/时 / 30 次/日 | 只计失败,不清零 |
| 管理端 TOTP 校验 | 账号 | 5 次/10min | |
| 管理端试发短信 | 全局 | 10 次/时 | |
| 支付回调 | IP | 120 次/分 | `webhooks_router.py` |
| Alertmanager webhook | IP | 120 次/分;报文 ≤1 MiB;≤500 条;字符串截 1024 | `notify/router.py` |
| 节点注册脚本 / bootstrap / progress | IP | 30 / 30 / 60 次/分 | `nodes/enroll_router.py` |
| 工单创建 | 用户 | 5 次/时 | |
| 实例日志 | 用户 | 20 次/时;tail 默认 200、≤2000 行;since ≤86400s;K8s 读 5s 超时 | `orchestrator/service.py` |
| 指标批量端点 | 用户 | 前 20 台 running 实例 | `metering/service.py` |

## 分页与批量上限

| 项 | 值 | 承载 |
|---|---|---|
| 游标分页 | 默认 20,最大 100 | `core/pagination.py` |
| 固定截断列表(公告、注销申请、发票管理列表、outbox 排障视图) | 200 | 各 service 的 `*_LIST_CAP` |
| 公告群发 | 单语句 1000 行,单事务 ⌈N/1000⌉ 条 | `notify/service.py` |
| outbox | 重试 5 次,退避 10s→600s,单任务超时 600s | `core/outbox.py` |
| K8s list | 每页 500 + continue;连接 5s / 读 30s 超时 | `core/k8s/real.py`,env `k8s_*_timeout_seconds` |
| Prometheus 查询 | 单次 5s 超时 | env `prometheus_timeout_seconds` |
| 集群连通性探测 | 5s → 502 | `nodes/service.py` |
| 调账单笔绝对值 | ≤100000.00 | `adminapi/service.py` `ADJUST_MAX_ABS` |

## 数据保留

| 数据 | 保留 | 承载 |
|---|---|---|
| `audit_log` | 365 天 | env `audit_retention_days`(等保 ≥6 个月) |
| `sms_codes` | 过期后 7 天删 | `workers/main.py` `cleanup_expired_rows`(每日) |
| `used_refresh_tokens` | 过期即删 | 同上 |
| `outbox_tasks`(done / discarded) | 7 天 | 同上 |
| `rate_limit_counters` | 2 天 | 同上 |
| `instance_events`、`balance_ledger`、账单表 | 不删;账号注销只匿名化用户行 | 法定义务 |
| `notifications` | 不删 | |
| `node_specs` Missing 行 | 7 天后删 | `nodes/patrol.py` |
| 容器与 apiserver 审计日志(Loki) | 180 天(full / light 同) | `deploy/cluster/values/loki.yaml` |
| Prometheus | full 15 天 / 40GB;light 3 天 / 3GB | `values/kps.yaml`、`values/light/kps-light.yaml` |
| etcd 快照 | 每 6h 一份,留 12 份 | `deploy/cluster/rke2/server-config.yaml` |
| PG 逻辑备份 | 每日一次(RPO 24h);连续归档 RPO 分钟级;恢复目标 RTO <30 分钟 | `deploy/app/k8s/06-pg-backup.yaml`、`deploy/cluster/runbooks/pg-backup-restore.md` |
