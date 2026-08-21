"""错误文案目录:AppError(key=...) 的单一事实源。

- 键命名 `<模块>.<camelCase 语义槽>`,wire 上不带 namespace 前缀(前端固定查 errors ns)。
- 占位符用 str.format 形式 `{name}`;导出脚本转为 i18next 的 `{{name}}`。
- 生成链:scripts/export_error_messages.py 读本表写 packages/ui/locales/zh-CN/errors.json
  (CI no-diff 锁);en 手译,键集与占位符 parity 由 packages/ui 的 locales.test 锁。
"""

from collections.abc import Mapping
from typing import Any

MESSAGES: dict[str, str] = {
    # 账户
    "account.credentialRequired": "需提供验证码或密码",
    "account.loginFailed": "手机号或凭证错误",
    "account.loginFailedPassword": "手机号或密码错误",
    "account.loginFailedSms": "手机号或验证码错误",
    "account.phoneTaken": "该手机号已注册,请直接登录",
    "account.realNameChannelError": "实名核验服务暂不可用,请稍后重试",
    "account.realNameDone": "已完成实名认证,无需重复提交",
    "account.realNameMismatch": "实名信息与运营商记录不一致,请核对后重试",
    "account.smsCodeInvalid": "验证码错误或已过期",
    "account.smsSendFailed": "短信发送失败,请稍后重试",
    "account.smsTooFrequent": "发送过于频繁,请 {seconds} 秒后再试",
    "account.sshKeyDuplicate": "该公钥已添加过",
    "account.termsNotAccepted": "请先阅读并同意《用户协议》与《隐私政策》",
    "account.userFrozen": "账号已被冻结,请联系客服",
    # 管理端
    "adminapi.adjustAlreadyProcessed": "调账单已处理",
    "adminapi.adjustNotZero": "调账金额不能为 0",
    "adminapi.adjustSecondReviewer": "调账必须由第二位管理员复核",
    "adminapi.badDayFormat": "day 格式应为 YYYY-MM-DD",
    "adminapi.loginFailed": "用户名或密码错误",
    "adminapi.smsTestFailed": "发送失败:{message}",
    "adminapi.taskNotFound": "任务不存在",
    "adminapi.taskStateNotIgnorable": "任务状态 {status} 不可忽略",
    "adminapi.taskStateNotReplayable": "任务状态 {status} 不可重放",
    "adminapi.userDisabled": "账号已停用",
    # 计费/支付
    "billing.alipayCallbackVerifyFailed": "支付宝回调验签失败",
    "billing.alipayCreateFailed": "支付宝下单失败:{message}",
    "billing.alipayCredentialsIncomplete": "支付宝商户凭据不完整(管理端·平台配置)",
    "billing.alipayQueryFailed": "支付宝查单失败:{message}",
    "billing.amountMismatchAdjust": "渠道金额 {channel} 与订单金额 {order} 不符,需人工调账处理",
    "billing.badDateFormat": "日期格式应为 YYYY-MM-DD",
    "billing.badMonthFormat": "月份格式应为 YYYY-MM",
    "billing.callbackAmountMismatch": "回调金额与订单不符",
    "billing.callbackChannelMismatch": "回调渠道与订单不符",
    "billing.channelNotEnabled": "该支付渠道暂未开通,请选择其他支付方式",
    "billing.channelStateNotBackfillable": "渠道侧状态为 {status},不能补单",
    "billing.insufficientBalance": "余额不足,请先充值",
    "billing.insufficientForDisk": "余额不足:新建数据盘需要至少 1 日费用 ¥{amount}",
    "billing.insufficientForRestart": "余额不足:重启需要至少 1 小时预估费用",
    "billing.insufficientForStart": "余额不足:开机需要至少 1 小时预估费用 ¥{amount}",
    "billing.mockCallbackParseFailed": "mock 回调解析失败",
    "billing.mockDevOnly": "mock 渠道仅限开发环境",
    "billing.orderAlreadyPaid": "订单已入账,无需补单",
    "billing.orderNotFound": "订单不存在",
    "billing.orderStateNotBackfillable": "订单状态 {status} 不可补单",
    "billing.realNameRequiredForRecharge": "按监管要求,充值前需完成实名认证",
    "billing.rechargeAmountRange": "充值金额须在 {min}~{max} 元之间",
    "billing.unknownChannel": "未知支付渠道:{name}",
    "billing.wechatCallbackVerifyFailed": "微信回调验签失败",
    "billing.wechatCreateFailed": "微信下单失败:{message}",
    "billing.wechatCredentialsIncomplete": "微信支付商户凭据不完整(管理端·平台配置)",
    "billing.wechatPublicKeyPair": "微信支付公钥模式需同时配置公钥与公钥 ID",
    "billing.wechatQueryFailed": "微信查单失败:{message}",
    # 商品/镜像
    "catalog.imageRefExists": "镜像 image_ref 已存在",
    "catalog.prewarmDisabled": "该镜像已关闭预热,请先开启",
    "catalog.skuNotSellable": (
        "集群中没有「{model} × {pool} 池」的 Ready 节点,上架后用户将无法开机;确认可强制上架"
    ),
    "catalog.skuOffSale": "该规格已下架",
    # 通用兜底(errors.py 三个 helper 与 422/500 handler 使用)
    "common.forbidden": "无权访问",
    "common.internal": "服务器内部错误,请稍后重试",
    "common.notFound": "资源不存在",
    "nodes.clusterProbeFailed": "集群连接失败:{error}",
    "nodes.clusterNotReady": "集群调度组件未就绪,暂时无法开机;平台正在自动检测恢复,请稍后重试",
    "common.unauthorized": "未登录或凭证已过期",
    "common.badCursor": "无效的分页游标",
    "common.rateLimited": "尝试过于频繁,请稍后再试",
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
    "metering.badNodeName": "节点名不合法",
    "metering.badRange": "range 须为 1h/6h/24h",
    "metering.unavailable": "监控数据暂不可用,不影响计费(计费依据为实例事件流水)",
    # 节点接入
    "nodes.alreadyTerminal": "状态 {status} 已是终态,无需吊销",
    "nodes.clusterNotConfigured": (
        "集群接入参数未配置:请超管在「平台配置 · 集群接入」录入 RKE2 Server 地址与 join token"
    ),
    "nodes.enrollTransition": "注册状态不允许 {from} → {to}",
    "nodes.hostnameMismatch": "主机名与登记不符,令牌已作废,请在管理端核对后重新生成",
    "nodes.regenerateNotAllowed": "状态 {status} 不允许重新生成(仅 待执行/已过期/已失败)",
    # 实例编排
    "orchestrator.accessNeedsRunning": "实例运行中才能获取接入信息",
    "orchestrator.forceStopNeedsRunning": "仅运行中的实例可以强制停止",
    "orchestrator.frozenNeedsRecharge": "实例已因欠费冻结,充值解冻后可开机",
    "orchestrator.gpuCountRange": "GPU 数量须在 1~{max} 之间",
    "orchestrator.gpuQuota": "GPU 总数将超过上限({max} 卡),请释放后再创建或联系客服提额",
    "orchestrator.imageRefInvalid": "镜像地址格式不正确,示例:registry.example.com/pytorch:2.9",
    "orchestrator.imageRefNotAllowed": "该镜像仓库未被允许,请使用平台镜像或以下仓库:{registries}",
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
