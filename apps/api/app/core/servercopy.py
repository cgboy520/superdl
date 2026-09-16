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
}


def copy(key: str, *, locale: str | None = None, **params: object) -> str:
    """Render the copy for key in the deployment's audience locale (or an explicit locale)."""
    table = _COPY[key]
    loc = locale or current_profile().default_locale
    return (table.get(loc) or table[DEFAULT_LOCALE]).format(**params)
