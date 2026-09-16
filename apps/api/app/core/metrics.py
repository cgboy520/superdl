"""Prometheus counters, gauges and histograms exposed through /metrics."""

from prometheus_client import Counter, Gauge, Histogram

OUTBOX_DEAD_TOTAL = Counter(
    "superdl_outbox_dead_total", "outbox tasks that exhausted retries and went dead", ["task_type"]
)
SETTLEMENT_FAILED_TOTAL = Counter(
    "superdl_settlement_failed_total", "settlement failures (hourly / daily by kind)", ["kind"]
)
SETTLEMENT_LAG = Gauge(
    "superdl_settlement_lag_windows",
    "windows the settlement watermark lags behind now (hourly in hours / daily_disk in days;"
    " >1 = unsettled windows)",
    ["kind"],
)
OUTBOX_TASK_TIMEOUT_TOTAL = Counter(
    "superdl_outbox_task_timeout_total",
    "outbox tasks interrupted for exceeding the execution cap (head-of-queue stall)",
    ["task_type"],
)
FUND_RECONCILE_MISMATCH_TOTAL = Counter(
    "superdl_fund_reconcile_mismatch_total",
    "fund reconciliation discrepancies (wallet_ledger: balance != ledger sum;"
    " bill_consume: billed != consumed)",
    ["kind"],
)
SETTLEMENT_GAP_UNRESOLVED = Gauge(
    "superdl_settlement_gap_unresolved",
    "unresolved settlement gaps (DB view; refreshed every settlement round, alert on >0 sustained,"
    " gaps do not self-heal)",
    ["kind"],
)
PATROL_FAILED_TOTAL = Counter(
    "superdl_patrol_failed_total", "balance patrol failures per stage", ["stage"]
)
PAYMENT_RECOVER_FAILED_TOTAL = Counter(
    "superdl_payment_recover_failed_total",
    "order-query recovery credit failures (amount / channel mismatch, unique constraint ...;"
    " the round continues)",
    ["error"],
)
RECONCILE_LEAKED_TOTAL = Counter(
    "superdl_reconcile_leaked_total", "leaked Pods reclaimed by the reconciler"
)
DISK_PROVISION_FAILED_TOTAL = Counter(
    "superdl_disk_provision_failed_total",
    "data-disk PVC create / grow dead letters (counted before each re-dispatch; an unprovisioned"
    " disk cannot be mounted)",
)
INSTANCE_NODE_LOST_TOTAL = Counter(
    "superdl_instance_node_lost_total",
    "instances stopped because their node was lost (Pod not-ready for too long)",
)
SPOT_PREEMPTED_TOTAL = Counter(
    "superdl_spot_preempted_total",
    "spot instances reclaimed by preemption (platform-initiated, not user stops)",
)
RECONCILE_STUCK_INSTANCES = Gauge(
    "superdl_reconcile_stuck_instances",
    "stuck instances (in stopping/releasing past the first timeout without converging)",
    ["status"],
)
LIGHT_DISTRO_IN_PROD = Gauge(
    "superdl_light_distro_in_prod",
    "prod running on k3s (light tier) = 1: tenants share the host with the control plane, not for"
    " public production",
)
NODE_POOL_LABEL_MISMATCH_TOTAL = Counter(
    "superdl_node_pool_label_mismatch_total",
    "corrections of a node pool label that disagreed with the enrollment (node_enrollments.pool);"
    " non-zero is abnormal",
)
NODE_UNENROLLED = Gauge(
    "superdl_node_unenrolled",
    "non-infra nodes without an enrollment row, desired pool or pool label; non-zero = an unknown"
    " node joined and the patrol cordoned it",
)
RECONCILE_LEAK_ABORTED_TOTAL = Counter(
    "superdl_reconcile_leak_aborted_total",
    "leak-reclamation rounds aborted because the unknown-Pod share exceeded the threshold"
    " (monotonic; alert on increase)",
)
AUDIT_WRITE_FAILED_TOTAL = Counter(
    "superdl_audit_write_failed_total",
    "audit row write failures (fail-open independent-session path; money-domain actions audit"
    " synchronously in the same transaction and are unaffected)",
)
LOGIN_FAILED_TOTAL = Counter(
    "superdl_login_failed_total",
    "login failures (wrong credentials / unknown account; actor_type separates user and admin)",
    ["actor_type"],
)
AUTHZ_DENIED_TOTAL = Counter(
    "superdl_authz_denied_total",
    "authenticated requests rejected by the role gate (403); sustained non-zero = privilege probing"
    " or frontend menu out of step with the backend gate",
    ["actor_type"],
)
ADMIN_PRIVILEGE_CHANGE_TOTAL = Counter(
    "superdl_admin_privilege_change_total",
    "admin privilege-surface changes (account creation, role change / deactivation, password reset,"
    " MFA reset); every one should be claimed",
)
PII_REVEAL_ROWS_TOTAL = Counter(
    "superdl_pii_reveal_rows_total",
    "PII rows revealed in plaintext to admins (kind: tenant KYC identity / invoice title, email)",
    ["kind"],
)
PAYMENT_CALLBACK_MISMATCH_TOTAL = Counter(
    "superdl_payment_callback_mismatch_total",
    "payment callbacks whose amount disagrees with the order",
)
PAYMENT_CLOSED_ORDER_RESCUED_TOTAL = Counter(
    "superdl_payment_closed_order_rescued_total",
    "valid success callbacks credited after the order was closed (non-zero = local close earlier"
    " than the channel expiry)",
)
PAYMENT_CHANNEL_REVERSED_TOTAL = Counter(
    "superdl_payment_channel_reversed_total",
    "channel close / refund notices on credited orders (no automatic reversal, manual write-off;"
    " alert PaymentChannelReversed)",
)
PAYMENT_REVERSAL_RESOLVED_TOTAL = Counter(
    "superdl_payment_reversal_resolved_total",
    "manual handling of channel reversal notices (action=release unfreeze / chargeback confirmed);"
    " every release alerts",
    ["action"],
)
PLATFORM_CONFIG_WRITE_TOTAL = Counter(
    "superdl_platform_config_write_total",
    "platform configuration writes (domain = setting group; payment/crypto writes change the"
    " payment verification root and alert every time)",
    ["domain"],
)
ENDPOINT_AUTH_DENIED_TOTAL = Counter(
    "superdl_endpoint_auth_denied_total",
    "service endpoint gateway auth callback denials (unknown / revoked key, service not ready);"
    " sustained non-zero = someone guessing keys",
)
USER_SIGNUP_TOTAL = Counter(
    "superdl_user_signup_total", "successful user sign-ups (drives the sign-up velocity alert)"
)
VERIFICATION_SENT_TOTAL = Counter(
    "superdl_verification_sent_total",
    "Verification codes sent by channel (sms / email) and purpose; cost and abuse alerts",
    ["channel", "purpose"],
)
SSH_PORT_POOL = Gauge(
    "superdl_ssh_port_pool_ports",
    "SSH NodePort pool level (state=assigned / blocked / free); refreshed every reconciler round",
    ["state"],
)
WALLET_NEGATIVE_COUNT = Gauge(
    "superdl_wallet_negative_count",
    "wallets with a negative balance (refreshed every balance patrol round)",
)
WALLET_NEGATIVE_SUM = Gauge(
    "superdl_wallet_negative_sum",
    "Sum of negative balances (absolute, platform currency; display only; settlement may overdraw)",
)
WORKER_HEARTBEAT_TS = Gauge(
    "superdl_worker_heartbeat_timestamp_seconds",
    "Unix timestamp of the worker main loop's latest heartbeat",
)
OUTBOX_PENDING_OLDEST_AGE = Gauge(
    "superdl_outbox_pending_oldest_age_seconds",
    "age in seconds of the oldest pending outbox task. Sustained >600 = consumption stalled: the"
    " worker is alive but cannot claim tasks (e.g. a locked_by overflow silently stops claims);"
    " heartbeat / probes / OUTBOX_DEAD_TOTAL all look normal then, this metric is the only signal",
)
HTTP_REQUEST_DURATION = Histogram(
    "superdl_http_request_duration_seconds",
    "HTTP request latency (route is the route template, avoiding high cardinality)",
    ["method", "route", "status"],
)
RUNTIME_CONFIG = Gauge(
    "superdl_runtime_config",
    "effective runtime hardening surface (always 1; labels are the effective values:"
    " environment/k8s_backend/payment_mock)",
    ["environment", "k8s_backend", "payment_mock"],
)
SUBSCRIPTION_UNPAID_RUNNING = Gauge(
    "superdl_subscription_unpaid_running_instances",
    "instances with market=subscription in creating/starting/running without a covering"
    " subscription (refreshed every subscription patrol round)",
)
SCHEDULE_TIMEOUT_OCCUPIED_TOTAL = Counter(
    "superdl_schedule_timeout_occupied_total",
    "service instances failed on health_path timeout whose container had actually started (the"
    " occupied stretch is billed on demand)",
)
