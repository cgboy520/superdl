"""通过 /metrics 暴露的 Prometheus 计数器、仪表和直方图。"""

from prometheus_client import Counter, Gauge, Histogram

OUTBOX_DEAD_TOTAL = Counter(
    "superdl_outbox_dead_total", "outbox 任务重试耗尽进入死信的次数", ["task_type"]
)
SETTLEMENT_FAILED_TOTAL = Counter(
    "superdl_settlement_failed_total", "结算失败次数(小时结算/日结按 kind 区分)", ["kind"]
)
SETTLEMENT_LAG = Gauge(
    "superdl_settlement_lag_windows",
    "结算水位线落后当前的窗口数(hourly 计小时 / daily_disk 计日;>1 即有窗口未追平)",
    ["kind"],
)
OUTBOX_TASK_TIMEOUT_TOTAL = Counter(
    "superdl_outbox_task_timeout_total",
    "超过执行上限被中断的 outbox 任务数(队头卡死)",
    ["task_type"],
)
FUND_RECONCILE_MISMATCH_TOTAL = Counter(
    "superdl_fund_reconcile_mismatch_total",
    "资金账实核对发现的差异数(wallet_ledger:余额≠流水累计;bill_consume:出账≠消费流水)",
    ["kind"],
)
SETTLEMENT_GAP_UNRESOLVED = Gauge(
    "superdl_settlement_gap_unresolved",
    "未核销结算缺口数(DB 口径;结算任务每轮刷新,告警按 >0 持续判,缺口不自愈)",
    ["kind"],
)
PATROL_FAILED_TOTAL = Counter(
    "superdl_patrol_failed_total", "余额巡检各环节异常次数(按阶段区分)", ["stage"]
)
PAYMENT_RECOVER_FAILED_TOTAL = Counter(
    "superdl_payment_recover_failed_total",
    "查单收敛单笔入账失败次数(金额/渠道不符、唯一约束等;不中断整轮)",
    ["error"],
)
RECONCILE_LEAKED_TOTAL = Counter("superdl_reconcile_leaked_total", "reconciler 回收的泄漏 Pod 数")
DISK_PROVISION_FAILED_TOTAL = Counter(
    "superdl_disk_provision_failed_total",
    "数据盘 PVC 建/扩死信次数(每次重派前计一次;未就绪的盘不可挂载)",
)
INSTANCE_NODE_LOST_TOTAL = Counter(
    "superdl_instance_node_lost_total",
    "因节点失联(Pod 持续 not-ready)被判定停止的实例数",
)
SPOT_PREEMPTED_TOTAL = Counter(
    "superdl_spot_preempted_total",
    "被抢占回收的竞价实例数(平台主动回收,不含用户自己关机)",
)
RECONCILE_STUCK_INSTANCES = Gauge(
    "superdl_reconcile_stuck_instances",
    "悬挂实例数(进入 stopping/releasing 超过第一档超时仍未收敛)",
    ["status"],
)
LIGHT_DISTRO_IN_PROD = Gauge(
    "superdl_light_distro_in_prod",
    "prod 环境运行在 k3s(light 档)= 1:light 档租户与控制面同宿主,禁止公众生产",
)
NODE_POOL_LABEL_MISMATCH_TOTAL = Counter(
    "superdl_node_pool_label_mismatch_total",
    "节点自声明池标签与平台注册登记(node_enrollments.pool)不符的纠正次数;非零即异常",
)
NODE_UNENROLLED = Gauge(
    "superdl_node_unenrolled",
    "无登记行、无期望池且未打池标签的非 infra 节点数;非零即有未知节点入群,巡检已自动 cordon",
)
RECONCILE_LEAK_ABORTED_TOTAL = Counter(
    "superdl_reconcile_leak_aborted_total",
    "泄漏回收因未知 Pod 占比超阈被熔断中止的轮数(单调不降,告警按 increase 判)",
)
AUDIT_WRITE_FAILED_TOTAL = Counter(
    "superdl_audit_write_failed_total",
    "审计行写入失败次数(fail-open 独立 session 路径;资金域动作为同事务同步审计,不受影响)",
)
LOGIN_FAILED_TOTAL = Counter(
    "superdl_login_failed_total",
    "登录失败次数(凭据错/账号不存在;actor_type 区分用户端与管理端)",
    ["actor_type"],
)
AUTHZ_DENIED_TOTAL = Counter(
    "superdl_authz_denied_total",
    "已认证但被角色门拒绝的请求数(403);持续非零 = 越权探测或前端菜单与后端角色门不同步",
    ["actor_type"],
)
ADMIN_PRIVILEGE_CHANGE_TOTAL = Counter(
    "superdl_admin_privilege_change_total",
    "管理员权限面变更次数(建号、改角色/停用、重置口令、重置 MFA);每一次都应有人认领",
)
PII_REVEAL_ROWS_TOTAL = Counter(
    "superdl_pii_reveal_rows_total",
    "管理端明文读取到的 PII 行数(kind 区分来源:租户实名 / 发票抬头邮箱)",
    ["kind"],
)
PAYMENT_CALLBACK_MISMATCH_TOTAL = Counter(
    "superdl_payment_callback_mismatch_total", "支付回调金额与订单不符次数"
)
PAYMENT_CLOSED_ORDER_RESCUED_TOTAL = Counter(
    "superdl_payment_closed_order_rescued_total",
    "关单后有效成功回调自动入账数(非零 = 本地关单早于渠道侧过期)",
)
PAYMENT_CHANNEL_REVERSED_TOTAL = Counter(
    "superdl_payment_channel_reversed_total",
    "已入账订单收到渠道关单/退款类通知的次数(不自动冲账,人工核销;告警 PaymentChannelReversed)",
)
PAYMENT_REVERSAL_RESOLVED_TOTAL = Counter(
    "superdl_payment_reversal_resolved_total",
    "渠道反向通知人工处置次数(action=release 解冻 / chargeback 坐实);release 条条告警",
    ["action"],
)
PLATFORM_CONFIG_WRITE_TOTAL = Counter(
    "superdl_platform_config_write_total",
    "平台配置写入次数(domain 为配置组;payment/crypto 组改动会改变收款验签根,条条告警)",
    ["domain"],
)
ENDPOINT_AUTH_DENIED_TOTAL = Counter(
    "superdl_endpoint_auth_denied_total",
    "服务端点网关鉴权回调拒绝次数(key 不存在/已吊销/服务未就绪);持续非零 = 有人在猜 key",
)
USER_SIGNUP_TOTAL = Counter("superdl_user_signup_total", "用户注册成功次数(注册速率告警据此)")
VERIFICATION_SENT_TOTAL = Counter(
    "superdl_verification_sent_total",
    "Verification codes sent by channel (sms / email) and purpose; cost and abuse alerts",
    ["channel", "purpose"],
)
SSH_PORT_POOL = Gauge(
    "superdl_ssh_port_pool_ports",
    "SSH NodePort 端口池水位(state=assigned 已分配 / blocked 封禁 / free 空闲);reconciler 每轮刷新",
    ["state"],
)
WALLET_NEGATIVE_COUNT = Gauge("superdl_wallet_negative_count", "余额为负的钱包数(余额巡检每轮刷新)")
WALLET_NEGATIVE_SUM = Gauge(
    "superdl_wallet_negative_sum",
    "Sum of negative balances (absolute, platform currency; display only; settlement may overdraw)",
)
WORKER_HEARTBEAT_TS = Gauge(
    "superdl_worker_heartbeat_timestamp_seconds",
    "worker 主循环最近一次心跳的 Unix 时间戳",
)
OUTBOX_PENDING_OLDEST_AGE = Gauge(
    "superdl_outbox_pending_oldest_age_seconds",
    "最老 pending outbox 任务年龄(秒)。持续 >600 = 消费停滞:worker 活着但领不动任务"
    "(如 locked_by 列溢出致 claim 静默停摆)——此时心跳/探针/OUTBOX_DEAD_TOTAL 全部正常,"
    "本指标是唯一可观测出口",
)
HTTP_REQUEST_DURATION = Histogram(
    "superdl_http_request_duration_seconds",
    "HTTP 请求时延(route 为路由模板,避免高基数)",
    ["method", "route", "status"],
)
RUNTIME_CONFIG = Gauge(
    "superdl_runtime_config",
    "生效的运行时加固面(恒 1;标签即生效值:environment/k8s_backend/payment_mock)",
    ["environment", "k8s_backend", "payment_mock"],
)
SUBSCRIPTION_UNPAID_RUNNING = Gauge(
    "superdl_subscription_unpaid_running_instances",
    "market=subscription 且处于 creating/starting/running 但无在保订阅的实例数(订阅巡检每轮刷新)",
)
SCHEDULE_TIMEOUT_OCCUPIED_TOTAL = Counter(
    "superdl_schedule_timeout_occupied_total",
    "服务型实例 health_path 超时判失败且容器已实际运行的次数(占用时段已按量出账)",
)
