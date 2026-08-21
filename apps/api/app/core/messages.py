"""错误文案目录:AppError(key=...) 的单一事实源。

- 键命名 `<模块>.<camelCase 语义槽>`,wire 上不带 namespace 前缀(前端固定查 errors ns)。
- 占位符用 str.format 形式 `{name}`;导出脚本转为 i18next 的 `{{name}}`。
- 生成链:scripts/export_error_messages.py 读本表写 packages/ui/locales/zh-CN/errors.json
  (CI no-diff 锁);en 手译,键集与占位符 parity 由 packages/ui 的 locales.test 锁。
"""

from collections.abc import Mapping
from typing import Any

MESSAGES: dict[str, str] = {
    # 商品/镜像
    "catalog.imageRefExists": "镜像 image_ref 已存在",
    "catalog.prewarmDisabled": "该镜像已关闭预热,请先开启",
    "catalog.skuOffSale": "该规格已下架",
    # 通用兜底(errors.py 三个 helper 与 422/500 handler 使用)
    "common.forbidden": "无权访问",
    "common.internal": "服务器内部错误,请稍后重试",
    "common.notFound": "资源不存在",
    "common.unauthorized": "未登录或凭证已过期",
    "common.validation": "参数校验失败",
    # 数据盘
    "disks.expandNeedsActive": "仅正常状态的数据盘可以扩容",
    "disks.inUseDelete": "数据盘挂载中,请先释放对应实例",
    "disks.mountedElsewhere": "数据盘已挂载到其他实例",
    "disks.notMountable": "数据盘当前状态不可挂载",
    "disks.shrinkForbidden": "数据盘只支持扩容,不支持缩容",
    "disks.sizeMax": "容量上限 {max} GB",
    "disks.sizeRange": "容量须在 {min}~{max} GB 之间",
    # 计量/监控
    "metering.badRange": "range 须为 1h/6h/24h",
    "metering.unavailable": "监控数据暂不可用,不影响计费(计费依据为实例事件流水)",
    # 实例编排
    "orchestrator.accessNeedsRunning": "实例运行中才能获取接入信息",
    "orchestrator.forceStopNeedsRunning": "仅运行中的实例可以强制停止",
    "orchestrator.frozenNeedsRecharge": "实例已因欠费冻结,充值解冻后可开机",
    "orchestrator.gpuCountRange": "GPU 数量须在 1~{max} 之间",
    "orchestrator.gpuQuota": "GPU 总数将超过上限({max} 卡),请释放后再创建或联系客服提额",
    "orchestrator.instanceQuota": "实例数已达上限({max} 台),请释放后再创建或联系客服提额",
    "orchestrator.invalidTransition": "实例当前状态({from})不允许该操作",
    "orchestrator.releaseNeedsStopped": "关机后才能释放实例",
    "orchestrator.restartNeedsRunning": "仅运行中的实例可以重启",
    "orchestrator.sshKeyRequired": "请至少选择一个 SSH 公钥(实例仅支持密钥登录)",
    "orchestrator.sshPortsExhausted": "当前无可分配的 SSH 端口,请稍后重试或联系客服",
    "orchestrator.startNeedsStopped": "仅已关机的实例可以开机",
    "orchestrator.stateChangedRetry": "实例状态已被其他操作变更,请刷新后重试",
    "orchestrator.stopNeedsRunning": "仅运行中的实例可以关机",
}


def render_message(key: str, params: Mapping[str, Any] | None) -> str:
    """按目录渲染中文兜底文案。缺键/缺参回落并留痕,绝不因此 500。"""
    from app.core.logging import get_logger

    template = MESSAGES.get(key)
    if template is None:
        get_logger("app.messages").warning("message_key_missing", key=key)
        return key
    try:
        return template.format(**(params or {}))
    except (KeyError, IndexError):
        get_logger("app.messages").warning("message_params_mismatch", key=key)
        return template
