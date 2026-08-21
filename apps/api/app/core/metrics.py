"""业务指标(prometheus_client)。/metrics 暴露,kps 侧配套告警规则。

命名遵循 superdl_<domain>_<event>_total。
"""

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
    "超过执行上限被中断的 outbox 任务数(队头卡死;心跳与任务解耦后这是唯一的卡死信号)",
    ["task_type"],
)
FUND_RECONCILE_MISMATCH_TOTAL = Counter(
    "superdl_fund_reconcile_mismatch_total",
    "资金账实核对发现的差异数(wallet_ledger:余额≠流水累计;bill_consume:出账≠消费流水)",
    ["kind"],
)
RECONCILE_LEAKED_TOTAL = Counter("superdl_reconcile_leaked_total", "reconciler 回收的泄漏 Pod 数")
INSTANCE_NODE_LOST_TOTAL = Counter(
    "superdl_instance_node_lost_total",
    "因节点失联(Pod 持续 not-ready)被判定停止的实例数(每一条 = 一个付了钱但机器不可用的用户)",
)
PREWARM_NODES = Gauge(
    "superdl_prewarm_nodes",
    "每镜像×状态的节点数(预热覆盖;巡检末尾全量刷新)",
    ["image_ref", "status"],
)
PREWARM_FAILED_TOTAL = Counter(
    "superdl_prewarm_failed_total", "镜像预热拉取失败次数", ["image_ref"]
)
PAYMENT_CALLBACK_MISMATCH_TOTAL = Counter(
    "superdl_payment_callback_mismatch_total", "支付回调金额与订单不符次数"
)
PAYMENT_LOST_CALLBACK_RECOVERED_TOTAL = Counter(
    "superdl_payment_lost_callback_recovered_total", "查单 poller 收敛的丢回调订单数"
)
PAYMENT_CLOSED_ORDER_RESCUED_TOTAL = Counter(
    "superdl_payment_closed_order_rescued_total",
    "关单后有效成功回调自动入账数(非零说明本地关单早于渠道侧过期,需核对 TTL)",
)
# WorkerDown 告警依据(带 label 的 Counter 在首次 inc 前无序列,不能用 absent 判活)
WORKER_HEARTBEAT_TS = Gauge(
    "superdl_worker_heartbeat_timestamp_seconds",
    "worker 主循环最近一次心跳的 Unix 时间戳",
)
HTTP_REQUEST_DURATION = Histogram(
    "superdl_http_request_duration_seconds",
    "HTTP 请求时延(route 为路由模板,避免高基数)",
    ["method", "route", "status"],
)
