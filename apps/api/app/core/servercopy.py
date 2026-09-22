"""Server-rendered user-facing copy (in-app notifications, admin alerts, ledger remarks).

These strings are stored rendered and read later by any client, so they are rendered in the
deployment's audience language - the compliance profile's default locale - not the request
language. Placeholders use str.format names; every key has an en-US entry, zh-CN falls back to it.
"""

from app.core.compliance import current_profile
from app.core.locale import DEFAULT_LOCALE

_COPY: dict[str, dict[str, str]] = {
    "orchestrator.admin_force_stop.title": {
        "en-US": "Instance force-stopped by an administrator",
        "zh-CN": "实例已被管理员强制停止",  # cjk-ok
    },
    "orchestrator.admin_force_stop.content": {
        "en-US": 'Instance "{name}" was force-stopped and its tail bill settled. Reason: {reason}',
        "zh-CN": "实例「{name}」已被强制停止并结算尾账。原因:{reason}",  # cjk-ok
    },
    "orchestrator.restart_no_balance.title": {
        "en-US": "Restart not completed: insufficient balance",
        "zh-CN": "重启未完成:余额不足",  # cjk-ok
    },
    "orchestrator.restart_no_balance.content": {
        "en-US": (
            'Instance "{name}" has been stopped; the balance does not cover one estimated hour.'
            " Top up and start it yourself."
        ),
        "zh-CN": "实例「{name}」已关机;余额不足以支付 1 小时预估费用,充值后可自行开机。",  # cjk-ok
    },
    "orchestrator.restart_subscription_expired.title": {
        "en-US": "Restart not completed: subscription expired",
        "zh-CN": "重启未完成:包周期已到期",  # cjk-ok
    },
    "orchestrator.restart_subscription_expired.content": {
        "en-US": (
            'Instance "{name}" has been stopped; its subscription has expired. Renew and start it'
            " yourself."
        ),
        "zh-CN": "实例「{name}」已关机;包周期已到期,续费后可自行开机。",  # cjk-ok
    },
    "orchestrator.health_timeout.title": {
        "en-US": "Service failed to start: health check timed out",
        "zh-CN": "服务启动失败:健康检查超时",  # cjk-ok
    },
    "orchestrator.health_timeout.content": {
        "en-US": (
            'Service instance "{name}" did not pass the health_path check within the timeout and'
            " was terminated; the time the container actually ran was billed on demand and"
            " subscription prepayments are not refunded. Fix the health check and start it again."
        ),
        "zh-CN": (
            "服务实例「{name}」在超时内未通过 health_path 健康检查,已自动终止;"  # cjk-ok
            "容器实际运行时段已按量计费,包周期预付不退。修复健康检查后可重新启动。"  # cjk-ok
        ),
    },
    "orchestrator.schedule_timeout.title": {
        "en-US": "Instance creation failed: scheduling timed out",
        "zh-CN": "实例创建失败:调度超时",  # cjk-ok
    },
    "orchestrator.schedule_timeout.content": {
        "en-US": (
            'Instance "{name}" timed out while scheduling or pulling its image and was terminated;'
            " {charge} Try another tier or try again later; contact support if it keeps failing."
        ),
        "zh-CN": (
            "实例「{name}」调度或镜像拉取超时,已自动终止,{charge}"  # cjk-ok
            "可换个档位重试,或稍后再试;多次失败请联系客服。"  # cjk-ok
        ),
    },
    "orchestrator.schedule_timeout.refunded": {
        "en-US": "the subscription prepayment of {amount} was returned to your balance in full.",
        "zh-CN": "包周期预付 {amount} 已原额退回余额。",  # cjk-ok
    },
    "orchestrator.schedule_timeout.no_charge": {
        "en-US": "nothing was charged.",
        "zh-CN": "未产生任何费用。",  # cjk-ok
    },
    "orchestrator.node_lost.title": {
        "en-US": "Instance stopped: its node lost contact",
        "zh-CN": "实例已停止:所在节点失联",  # cjk-ok
    },
    "orchestrator.node_lost.content": {
        "en-US": (
            'The node hosting instance "{name}" lost contact with the cluster; billing stopped and'
            " the instance was terminated. Contact support if you dispute charges from the outage."
            " The instance disk is local to that node with no redundancy: the instance cannot start"
            " until the node recovers, and if the node is never recovered the data on that disk is"
            " lost - back up important data to a data disk or off-platform."
        ),
        "zh-CN": (
            "实例「{name}」所在节点与集群失去联系,已停止计费并终止该实例。"  # cjk-ok
            "失联期间产生的费用如有异议请联系客服。"  # cjk-ok
            "实例盘为该节点本地盘,平台不做冗余:节点恢复前该实例暂不可开机,"  # cjk-ok
            "若节点最终无法恢复,盘中数据将无法找回——"  # cjk-ok
            "重要数据请务必自行备份到数据盘或站外。"  # cjk-ok
        ),
    },
    "orchestrator.failed_retention.title": {
        "en-US": "Failed instance released automatically",
        "zh-CN": "失败实例已自动释放",  # cjk-ok
    },
    "orchestrator.failed_retention.content": {
        "en-US": (
            'Instance "{name}" stayed failed for more than {days} days and was released'
            " automatically (instance disk erased, data disks unaffected)."
        ),
        "zh-CN": (
            "实例「{name}」启动失败后超过 {days} 天未处理,已自动释放"  # cjk-ok
            "(实例盘清除,数据盘不受影响)。"  # cjk-ok
        ),
    },
    "orchestrator.retention_reclaim.title": {
        "en-US": "Stopped instance released automatically",
        "zh-CN": "停机实例已自动释放",  # cjk-ok
    },
    "orchestrator.retention_reclaim.content": {
        "en-US": (
            'Instance "{name}" was stopped for more than {days} days and was released under the'
            " retention policy (instance disk erased, data disks unaffected)."
        ),
        "zh-CN": (
            "实例「{name}」已停机超过 {days} 天,按保留期策略自动释放"  # cjk-ok
            "(实例盘清除,数据盘不受影响)。"  # cjk-ok
        ),
    },
    "orchestrator.retention_warn.title": {
        "en-US": "Stopped instance will be released soon",
        "zh-CN": "停机实例即将到期释放",  # cjk-ok
    },
    "orchestrator.retention_warn.content": {
        "en-US": (
            'Instance "{name}" has been stopped for more than {warn_days} days; after {days} days'
            " it is released automatically (instance disk erased, data disks unaffected). Start it"
            " to keep it, or back up and release it."
        ),
        "zh-CN": (
            "实例「{name}」已停机超过 {warn_days} 天;停机满 {days} 天将自动释放"  # cjk-ok
            "(实例盘清除,数据盘不受影响)。如需保留请开机或备份后释放。"  # cjk-ok
        ),
    },
    "services.rollout_failed.title": {
        "en-US": "Service revision update failed",
        "zh-CN": "服务版本更新失败",  # cjk-ok
    },
    "services.rollout_failed.content": {
        "en-US": (
            'Revision v{revision} of service "{name}" failed to start; the previous revision is'
            " kept (stopped) and can be started from the service detail page."
        ),
        "zh-CN": (
            "服务「{name}」的 v{revision} 启动失败,"  # cjk-ok
            "上一版本已保留(停机状态),可在服务详情启动上一版本。"  # cjk-ok
        ),
    },
    "catalog.price_change_24h.title": {
        "en-US": "SKU price changed sharply within 24 hours: {sku}",
        "zh-CN": "SKU 单价 24 小时累计大幅调整:{sku}",  # cjk-ok
    },
    "catalog.price_change_24h.content": {
        "en-US": (
            "{baseline}/h 24 hours ago → {new}/h now (cumulative change {pct}); this change"
            " {old} → {new_raw}; reason: {reason}"
        ),
        "zh-CN": (
            "24 小时前 {baseline} → 现 {new}/时(累计幅度 {pct});"  # cjk-ok
            "本次 {old} → {new_raw};原因:{reason}"  # cjk-ok
        ),
    },
    "catalog.price_change.title": {
        "en-US": "SKU price changed sharply: {sku}",
        "zh-CN": "SKU 单价大幅调整:{sku}",  # cjk-ok
    },
    "catalog.price_change.content": {
        "en-US": "{old}/h → {new}/h (change {pct}); reason: {reason}",
        "zh-CN": "{old} → {new}/时(幅度 {pct});原因:{reason}",  # cjk-ok
    },
    "notify.low_balance.title": {
        "en-US": "Low balance warning",
        "zh-CN": "余额不足预警",  # cjk-ok
    },
    "notify.low_balance.content": {
        "en-US": (
            "Current balance {balance}; at the current usage your instances can run for about"
            " {hours} more hours. Top up soon."
        ),
        "zh-CN": (
            "当前余额 {balance},"  # cjk-ok
            "按现有实例预计仅可再运行约 {hours} 小时,请及时充值。"  # cjk-ok
        ),
    },
    "notify.arrears.auto_stop": {
        "en-US": "Balance exhausted, instances stopped automatically",
        "zh-CN": "余额耗尽,实例已自动关机",  # cjk-ok
    },
    "notify.arrears.freeze": {"en-US": "Instances frozen", "zh-CN": "实例已冻结"},  # cjk-ok
    "notify.arrears.reclaim": {"en-US": "Instances reclaimed", "zh-CN": "实例已回收"},  # cjk-ok
    "notify.arrears.default": {"en-US": "Arrears notice", "zh-CN": "欠费通知"},  # cjk-ok
    "notify.subscription.expiring": {
        "en-US": "Subscription expiring soon",
        "zh-CN": "包周期即将到期",  # cjk-ok
    },
    "notify.subscription.expired": {
        "en-US": "Subscription expired, instance stopped",
        "zh-CN": "包周期已到期,实例已停机",  # cjk-ok
    },
    "notify.subscription.renewed": {
        "en-US": "Subscription renewed automatically",
        "zh-CN": "包周期已自动续费",  # cjk-ok
    },
    "notify.subscription.renew_failed": {
        "en-US": "Automatic renewal failed",
        "zh-CN": "自动续费失败",  # cjk-ok
    },
    "notify.subscription.default": {
        "en-US": "Subscription notice",
        "zh-CN": "包周期通知",  # cjk-ok
    },
    "notify.preempted.title": {
        "en-US": "Spot instance about to be reclaimed",
        "zh-CN": "竞价实例即将被回收",  # cjk-ok
    },
    "notify.preempted.content": {
        "en-US": (
            "{name} will be stopped in {seconds} seconds because the platform needs the capacity."
            " The instance disk is kept and you can start it again when capacity is available;"
            " the time run so far is settled by actual seconds."
        ),
        "zh-CN": (
            "{name} 因平台需要容量将在 {seconds} 秒后关机。"  # cjk-ok
            "实例盘保留,有容量时可自行开机;已运行时长按实际秒数结算。"  # cjk-ok
        ),
    },
    "notify.gpu_fault.title": {"en-US": "GPU hardware alert", "zh-CN": "GPU 硬件告警"},  # cjk-ok
    "notify.gpu_fault.content": {
        "en-US": (
            "The GPU hosting this instance raised a hardware fault alert and the platform is"
            " handling it. If the instance stops because of it, billing ends at the moment it"
            " stopped."
        ),
        "zh-CN": (
            "该实例所在 GPU 触发硬件故障告警,平台正在处理。"  # cjk-ok
            "若实例因此停机,将按停机瞬间结算,之后不再计费。"  # cjk-ok
        ),
    },
    "notify.oncall_sms.title": {
        "en-US": "[platform critical] {alertname}",
        "zh-CN": "[平台critical]{alertname}",  # cjk-ok
    },
    "tickets.created.title": {"en-US": "New ticket", "zh-CN": "新工单"},  # cjk-ok
    "tickets.reply.title": {"en-US": "New reply on a ticket", "zh-CN": "工单有新回复"},  # cjk-ok
    "tickets.staff_reply.content": {
        "en-US": "Support replied to your ticket {ticket_no} ({subject}); open Support to read it.",
        "zh-CN": "您的工单 {ticket_no}({subject})客服已回复,请前往「支持」查看。",  # cjk-ok
    },
    "tickets.stale.title": {
        "en-US": "Ticket waiting for more than 24 h",
        "zh-CN": "工单滞留超 24h",  # cjk-ok
    },
    "tickets.stale.content": {
        "en-US": (
            "{ticket_no} [{category}] {subject} has been waiting for a support reply for more than"
            " 24 hours; handle it soon."
        ),
        "zh-CN": (
            "{ticket_no} [{category}] {subject} 等待客服回复已超过 24 小时,请尽快处理。"  # cjk-ok
        ),
    },
    "adminapi.mfa_bound.title": {
        "en-US": "Administrator completed two-factor (TOTP) enrolment",
        "zh-CN": "管理员完成二要素(TOTP)绑定",  # cjk-ok
    },
    "adminapi.mfa_bound.content": {
        "en-US": (
            "Administrator {username} completed TOTP enrolment. If this was not them: have another"
            " admin reset their MFA and password at once and investigate a credential leak."
        ),
        "zh-CN": (
            "管理员 {username} 完成了 TOTP 绑定。"  # cjk-ok
            "若非本人操作:立即由另一位超管重置其 MFA 并改密排查口令泄漏。"  # cjk-ok
        ),
    },
    "adminapi.policy_moves.title": {
        "en-US": "Policy parameters changed sharply",
        "zh-CN": "策略参数大幅调整",  # cjk-ok
    },
    "adminapi.policy_moves.content": {
        "en-US": "{moves}; reason: {reason}",
        "zh-CN": "{moves};原因:{reason}",  # cjk-ok
    },
    "adminapi.unfrozen.title": {
        "en-US": "Account restored",
        "zh-CN": "账号已恢复正常",  # cjk-ok
    },
    "adminapi.unfrozen.content": {
        "en-US": (
            "Your account has been unfrozen. Instances stopped during the freeze must be started by"
            " you (instance disk data is kept)."
        ),
        "zh-CN": (
            "您的账号已解除冻结。"  # cjk-ok
            "冻结期间被停止的实例需要您手动开机(实例盘数据保留)。"  # cjk-ok
        ),
    },
    "adminapi.adjust.remark": {
        "en-US": "Adjustment: {reason}",
        "zh-CN": "调账:{reason}",  # cjk-ok
    },
    "adminapi.reversal.remark": {
        "en-US": "Channel reversal write-off: {reason}",
        "zh-CN": "渠道冲正核销:{reason}",  # cjk-ok
    },
    "account.login_anomaly.title": {
        "en-US": "Unusual sign-in attempts detected",
        "zh-CN": "检测到异常登录尝试",  # cjk-ok
    },
    "account.login_anomaly.content": {
        "en-US": (
            "Your account had {hits} failed sign-in attempts in the last 15 minutes; this sign-in"
            " succeeded. If this was not you, change your password now and review your account"
            " security."
        ),
        "zh-CN": (
            "您的账号近 15 分钟内有 {hits} 次登录失败记录,本次登录成功。"  # cjk-ok
            "若非本人操作,请立即修改密码并检查账号安全。"  # cjk-ok
        ),
    },
    "account.deletion_reject.leftovers": {
        "en-US": (
            "Auto-rejected: {instances} unreleased instance(s) ({instance_list}) and {disks}"
            " undeleted data disk(s) ({disk_list}) remain; clear them and apply again"
        ),
        "zh-CN": (
            "自动驳回:名下仍有未释放实例 {instances} 台({instance_list})、"  # cjk-ok
            "未删除数据盘 {disks} 块({disk_list});请先清空资源后重新申请"  # cjk-ok
        ),
    },
    "account.deletion_reject.balance": {
        "en-US": (
            "Auto-rejected: balance {balance} not withdrawn; withdraw it through a refund first and"
            " apply again once it arrives"
        ),
        "zh-CN": "自动驳回:余额 {balance} 未提现,请先经退款流程提现,到账后重新申请",  # cjk-ok
    },
    "billing.invoice_issued.title": {"en-US": "Invoice issued", "zh-CN": "发票已开具"},  # cjk-ok
    "billing.invoice_issued.content": {
        "en-US": (
            "Your invoice for period {period} (amount {amount}) has been issued, invoice number"
            " {invoice_no}; it will be sent to {email} within 1-3 business days."
        ),
        "zh-CN": (
            "您 {period} 账期的发票(金额 {amount})已开具,"  # cjk-ok
            "发票号 {invoice_no},将于 1-3 个工作日内发送至您的邮箱 {email}。"  # cjk-ok
        ),
    },
    "billing.invoice_rejected.title": {
        "en-US": "Invoice request rejected",
        "zh-CN": "发票申请被驳回",  # cjk-ok
    },
    "billing.invoice_rejected.content": {
        "en-US": (
            "Your invoice request for period {period} was rejected: {reason}. Correct the title"
            " information and submit again."
        ),
        "zh-CN": "您 {period} 账期的开票申请被驳回:{reason}。可修改抬头信息后重新提交。",  # cjk-ok
    },
    "billing.reconcile.title": {
        "en-US": "Fund reconciliation found discrepancies",
        "zh-CN": "资金账实核对发现差异",  # cjk-ok
    },
    "billing.reconcile.wallet_part": {
        "en-US": "{count} account(s) whose balance disagrees with the ledger sum",
        "zh-CN": "{count} 个账号的余额与流水累计不符",  # cjk-ok
    },
    "billing.reconcile.bill_part": {
        "en-US": "billed {billed} for the day does not match consumed ledger {consumed}",
        "zh-CN": "当日出账 {billed} 与消费流水 {consumed} 不符",  # cjk-ok
    },
    "billing.reconcile.content": {
        "en-US": "{parts}. Do not adjust accounts by hand; trace the source in balance_ledger.",
        "zh-CN": "{parts}。请勿自行改账,先按 balance_ledger 追溯来源。",  # cjk-ok
    },
    "billing.arrears.auto_stop.detail": {
        "en-US": "Balance exhausted; your instances were stopped automatically",
        "zh-CN": "余额耗尽,实例已自动关机",  # cjk-ok
    },
    "billing.arrears.freeze.detail": {
        "en-US": "Frozen for arrears; the instance disk is reclaimed in {hours} hours",
        "zh-CN": "欠费冻结,{hours} 小时后将回收实例盘",  # cjk-ok
    },
    "billing.arrears.reclaim.detail": {
        "en-US": "Freeze period over; the instance was reclaimed (instance disk erased, data disks"
        " kept)",
        "zh-CN": "冻结期满,实例已回收(实例盘清除,数据盘保留)",  # cjk-ok
    },
    "billing.period.day": {"en-US": "daily", "zh-CN": "日"},  # cjk-ok
    "billing.period.week": {"en-US": "weekly", "zh-CN": "周"},  # cjk-ok
    "billing.period.month": {"en-US": "monthly", "zh-CN": "月"},  # cjk-ok
    "billing.period.year": {"en-US": "yearly", "zh-CN": "年"},  # cjk-ok
    "billing.subscription.expiring.detail": {
        "en-US": (
            "Your {period} plan expires at {expires} UTC ({days} days left); the instance stops"
            " automatically on expiry. Renew in time."
        ),
        "zh-CN": (
            "包{period}将于 {expires} UTC 到期(剩 {days} 天),"  # cjk-ok
            "到期后自动停机。请及时续费。"  # cjk-ok
        ),
    },
    "billing.subscription.renew_failed.detail": {
        "en-US": (
            "Insufficient balance, automatic renewal failed and the instance will stop. Top up and"
            " renew manually."
        ),
        "zh-CN": "余额不足,自动续费失败,实例将停机。充值后可手动续费。",  # cjk-ok
    },
    "billing.subscription.renewed.detail": {
        "en-US": "Renewed automatically: {period} plan ×{count}, charged {amount}.",
        "zh-CN": "已自动续费 包{period}×{count},扣款 {amount}。",  # cjk-ok
    },
    "billing.subscription.expired.detail": {
        "en-US": (
            "The subscription expired and the instance was stopped; unless renewed within 72 hours"
            " the instance disk is reclaimed (data disks unaffected)."
        ),
        "zh-CN": "包周期已到期,实例已停机;72 小时内未续费将回收实例盘(数据盘不受影响)。",  # cjk-ok
    },
    "billing.remark.subscription": {
        "en-US": "{instance} {period} plan ×{count}",
        "zh-CN": "{instance} 包{period}×{count}",  # cjk-ok
    },
    "billing.remark.renewal": {
        "en-US": "{instance} renewal {period} plan ×{count}",
        "zh-CN": "{instance} 续费 包{period}×{count}",  # cjk-ok
    },
    "billing.remark.unstarted_refund": {
        "en-US": "Instance never started (scheduling timeout); subscription prepayment returned in"
        " full",
        "zh-CN": "实例调度超时未启动,包周期预付原额退回",  # cjk-ok
    },
    "billing.remark.refund": {
        "en-US": "Refund {refund_no} (order {order_no})",
        "zh-CN": "退款 {refund_no}(订单 {order_no})",  # cjk-ok
    },
    "billing.remark.hourly": {
        "en-US": "Instance GPU hourly fee ({source})",
        "zh-CN": "实例 GPU 时费({source})",  # cjk-ok
    },
    "billing.remark.fault_refund": {
        "en-US": "Hourly bill fault correction (instance event {event_id})",
        "zh-CN": "小时账单故障冲正(实例事件 {event_id})",  # cjk-ok
    },
    "billing.remark.reprice": {
        "en-US": "Instance GPU hourly fee (on-demand conversion top-up)",
        "zh-CN": "实例 GPU 时费(转按量补差价)",  # cjk-ok
    },
    "billing.remark.disk_daily": {
        "en-US": "Data disk daily fee",
        "zh-CN": "数据盘日常费用",  # cjk-ok
    },
    "billing.remark.reversal_freeze": {
        "en-US": "Channel reversal freeze",
        "zh-CN": "渠道冲正冻结",  # cjk-ok
    },
    "billing.remark.recharge": {"en-US": "{channel} top-up", "zh-CN": "{channel} 充值"},  # cjk-ok
    "billing.remark.recharge_backfill": {
        "en-US": "{channel} top-up (manual backfill)",
        "zh-CN": "{channel} 充值(人工补单)",  # cjk-ok
    },
    "billing.recharge.subject": {
        "en-US": "SuperDL top-up {order_no}",
        "zh-CN": "SuperDL 充值 {order_no}",  # cjk-ok
    },
    "billing.anomaly.lost_callback": {
        "en-US": "{channel} order pending for more than 10 minutes, callback probably lost",
        "zh-CN": "{channel} 渠道 pending 超 10 分钟,疑似回调丢失",  # cjk-ok
    },
    "billing.anomaly.closed_order": {
        "en-US": "Order closed on timeout; if the user claims to have paid, verify with the channel"
        " and backfill",
        "zh-CN": "订单超时关闭;若用户声称已付,先核验渠道再补单",  # cjk-ok
    },
    "billing.anomaly.failed_order": {
        "en-US": "Set to failed by an intermediate / failure callback; order-query recovery rescues"
        " paid orders automatically, manual backfill also works",
        "zh-CN": "渠道中间态/失败回调置 failed;查单收敛会自动救回已支付单,亦可人工补单",  # cjk-ok
    },
    "billing.anomaly.channel_reversed": {
        "en-US": (
            "Credited order received a channel close / refund notice: the wallet froze the same"
            " amount to block spending; after verification use"
            " /finance/reversals/<order_no>/resolve"
            " to release (noise) or charge back (confirmed reversal)"
        ),
        "zh-CN": (
            "已入账订单收到渠道关单/退款通知:钱包已等额冻结阻断消费;"  # cjk-ok
            "核实后经 /finance/reversals/<order_no>/resolve 解冻(噪音单)或扣回(确认反转)"  # cjk-ok
        ),
    },
    "billing.anomaly.negative_balance": {
        "en-US": "Negative wallet balance (left over after arrears reclamation), can be written off"
        " by adjustment",
        "zh-CN": "钱包负余额(欠费回收后残留),可调账核销",  # cjk-ok
    },
}


def copy(key: str, *, locale: str | None = None, **params: object) -> str:
    """Render the copy for key in the deployment's audience locale (or an explicit locale)."""
    table = _COPY[key]
    loc = locale or current_profile().default_locale
    return (table.get(loc) or table[DEFAULT_LOCALE]).format(**params)
