"""业务指标(prometheus_client)。/metrics 暴露,kps 侧配套告警规则。

命名遵循 superdl_<domain>_<event>_total;这些事件此前只写日志 —— 结算失败、
死信、泄漏 Pod、回调金额不符都是必须有人被叫醒的事。
"""

from prometheus_client import Counter, Gauge, Histogram

OUTBOX_DEAD_TOTAL = Counter(
    "superdl_outbox_dead_total", "outbox 任务重试耗尽进入死信的次数", ["task_type"]
)
SETTLEMENT_FAILED_TOTAL = Counter(
    "superdl_settlement_failed_total", "结算失败次数(小时结算/日结按 kind 区分)", ["kind"]
)
RECONCILE_LEAKED_TOTAL = Counter("superdl_reconcile_leaked_total", "reconciler 回收的泄漏 Pod 数")
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
# WorkerDown 告警依据:带 label 的 Counter 在首次 inc 前不产生任何序列,
# absent(superdl_outbox_dead_total) 会在 worker 健康时常驻误报 —— 心跳 Gauge 才是正解
WORKER_HEARTBEAT_TS = Gauge(
    "superdl_worker_heartbeat_timestamp_seconds",
    "worker 主循环最近一次心跳的 Unix 时间戳",
)
HTTP_REQUEST_DURATION = Histogram(
    "superdl_http_request_duration_seconds",
    "HTTP 请求时延(route 为路由模板,避免高基数)",
    ["method", "route", "status"],
)
