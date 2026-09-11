"""错误文案目录:AppError(key=...) 的单一事实源。键 `<模块>.<camelCase>`;占位符 `{name}`(导出转
`{{name}}`);scripts/export_error_messages.py 生成 packages/ui/locales/zh-CN/errors.json,en 手译。
"""

from collections.abc import Mapping
from typing import Any

from app.core.logging import get_logger

MESSAGES: dict[str, str] = {
    # 账户
    "account.accountDeleted": "账号已注销",
    "account.credentialRequired": "需提供验证码或密码",
    "account.deletionBalanceRemaining": (
        "余额 ¥{balance} 尚未提现:请先经退款流程提现,到账后再执行注销"
    ),
    "account.deletionCooldown": "注销冷静期未满(剩余约 {hours} 小时),暂不可执行",
    "account.deletionLeftovers": (
        "名下仍有未释放实例 {instances} 台、未删除数据盘 {disks} 块:请先清空资源后再注销"
    ),
    "account.deletionNotCancellable": "注销申请状态为 {status},不可撤销",
    "account.deletionNotPending": "注销申请状态为 {status},不可处理",
    "account.deletionPhoneMismatch": "手机号与当前账号不一致,请核对后重试",
    # 密码错 / 验证码错 / 未注册共用本条
    "account.loginFailed": "手机号或凭证错误",
    "account.phoneTaken": "该手机号已注册,请直接登录",
    "account.realNameChannelError": "实名核验服务暂不可用,请稍后重试",
    "account.realNameDisabled": "实名认证暂未开通",
    "account.realNameDone": "已完成实名认证,无需重复提交",
    "account.realNameMismatch": "实名信息与运营商记录不一致,请核对后重试",
    "account.smsCodeInvalid": "验证码错误或已过期",
    "account.smsSendFailed": "短信发送失败,请稍后重试",
    "account.smsTooFrequent": "发送过于频繁,请 {seconds} 秒后再试",
    "account.captchaRequired": "请先完成人机验证",
    "account.captchaVerifyFailed": "人机校验未通过,请重新完成验证后再试",
    "account.captchaChannelError": "人机校验服务暂不可用,请稍后重试",
    "account.sshKeyDuplicate": "该公钥已添加过",
    "account.termsNotAccepted": "请先阅读并同意《用户协议》与《隐私政策》",
    "account.userFrozen": "账号已被冻结,请联系客服",
    # 管理端
    "adminapi.adjustAlreadyProcessed": "调账单已处理",
    "adminapi.adjustNotZero": "调账金额不能为 0",
    "adminapi.alertAlreadyAcked": "该告警已确认",
    "adminapi.announcementAlreadyRevoked": "公告已撤回,无需重复操作",
    "adminapi.adjustSecondReviewer": "调账必须由第二位管理员复核",
    "adminapi.adjustReviewerTooNew": "复核账号在调账发起后才创建,不构成双人复核",
    "adminapi.badDayFormat": "day 格式应为 YYYY-MM-DD",
    "adminapi.adminUsernameTaken": "该用户名已存在",
    "adminapi.cannotChangeSelf": "不能停用或降低自己的权限,请让另一位超级管理员操作",
    "adminapi.loginFailed": "用户名或密码错误",
    "adminapi.roleRequired": "需要角色:{roles}",
    "adminapi.roleRequiredAdmin": "需要超级管理员权限",
    "adminapi.smsTestFailed": "发送失败:{message}",
    "adminapi.taskNotFound": "任务不存在",
    "adminapi.taskStateNotIgnorable": "任务状态 {status} 不可忽略",
    "adminapi.taskStateNotReplayable": "任务状态 {status} 不可重放",
    "adminapi.userDisabled": "账号已停用",
    "adminapi.mfaTicketInvalid": "登录票据已过期,请重新输入账号密码",
    "adminapi.mfaCodeInvalid": "动态码错误或已过期,请重试",
    "adminapi.mfaNotBound": "该账号未绑定动态口令",
    "adminapi.mfaResetSelfForbidden": "不能重置本人的动态口令:请用恢复码登录或找另一位超管",
    # 计费/支付
    "billing.alipayCallbackMerchantMismatch": "支付宝回调的商户信息与本平台不符",
    "billing.alipayCallbackVerifyFailed": "支付宝回调验签失败",
    "billing.alipayCreateFailed": "支付宝下单失败:{message}",
    "billing.alipayCredentialsIncomplete": "支付宝商户凭据不完整(管理端·平台配置)",
    "billing.alipayQueryFailed": "支付宝查单失败:{message}",
    "billing.alipaySellerIdRequired": (
        "支付宝渠道已启用但收款方 PID(seller_id)未配置(管理端·平台配置)"
    ),
    "billing.amountMismatchAdjust": "渠道金额 {channel} 与订单金额 {order} 不符,需人工调账处理",
    "billing.backfillKeyInUse": "该幂等键已用于补单 {order_no}:同键请复用原订单重放,换单请换键",
    "billing.badDateFormat": "日期格式应为 YYYY-MM-DD",
    "billing.badMonthFormat": "月份格式应为 YYYY-MM",
    "billing.callbackAmountMismatch": "回调金额与订单不符",
    "billing.callbackChannelMismatch": "回调渠道与订单不符",
    "billing.channelNotEnabled": "该支付渠道暂未开通,请选择其他支付方式",
    "billing.channelStateNotBackfillable": "渠道侧状态为 {status},不能补单",
    "billing.insufficientBalance": "余额不足,请先充值",
    "billing.insufficientAvailableFrozen": (
        "可用余额不足:¥{frozen} 因支付渠道冲正被冻结,待核销期间不可用于新消费;如有疑问请联系客服"
    ),
    "billing.settlementBehind": "结算正在追平,请稍后再转包周期",
    "billing.subscriptionAlreadyActive": "该实例已在包周期内,如需延长请使用续费",
    "billing.subscriptionCancelled": "该实例的包周期已作废,无法续费",
    "billing.subscriptionExpired": "包周期已到期,请先续费再开机",
    "billing.subscriptionMissing": "该实例没有可续费的包周期",
    "billing.insufficientForInFlight": (
        "余额不足:在途资源预计还要消耗 ¥{inflight},本次操作要求余额不少于 ¥{required}"
        "(当前 ¥{balance}),请先充值"
    ),
    "billing.invoiceNothingToBill": "账期 {period} 没有可开票金额(无已支付充值或已全部申请)",
    "billing.invoiceAmountStale": (
        "可开票金额已变动(当前可开 ¥{expected},申请额 ¥{requested}):"
        "账期内发生了退款,请驳回该申请并通知用户按新金额重新提交"
    ),
    "billing.invoiceNotFound": "发票申请不存在",
    "billing.invoicePeriodAlreadyApplied": "账期 {period} 已有申请中或已开票的发票,请勿重复提交",
    "billing.invoicePeriodNotOpen": "账期 {period} 尚未结束:当月账期请于次月 1 日后再申请",
    "billing.invoiceStateNotIssuable": "发票申请状态为 {status},仅「申请中」的发票可开票",
    "billing.invoiceStateNotRejectable": "发票申请状态为 {status},仅「申请中」的发票可驳回",
    "billing.mockCallbackParseFailed": "mock 回调解析失败",
    "billing.mockDevOnly": "mock 渠道仅限开发环境",
    "billing.orderAlreadyPaid": "订单已入账,无需补单",
    "billing.orderNotFound": "订单不存在",
    "billing.orderStateNotBackfillable": "订单状态 {status} 不可补单",
    "billing.realNameRequiredForRecharge": "按监管要求,充值前需完成实名认证",
    "billing.refundAlreadyApplied": "该订单已有进行中的退款申请,请勿重复提交",
    "billing.refundAmountExceeded": (
        "退款金额不能超过可退上限 ¥{max}(订单金额 ¥{order},已退 ¥{refunded},可退余额 ¥{refundable})"
    ),
    "billing.refundNotRefundable": (
        "可退余额不足(渠道实付扣除已消费/已退后剩 ¥{refundable},应退 ¥{amount});请取消该退款单"
    ),
    "billing.refundPayoutChannelMismatch": (
        "打款渠道须与订单支付渠道原路一致(应为 {expected});确需线下打款请选 offline 并留存凭证"
    ),
    "billing.refundCumulativeExceeded": (
        "累计退款将超过订单金额(订单 ¥{order},已退 ¥{refunded},本次 ¥{amount}):"
        "数据异常,请核查后取消该退款单"
    ),
    "billing.refundBalanceConsumed": (
        "余额已被消费,暂不能核销退款(当前余额 ¥{balance},应退 ¥{amount});请取消该退款单"
    ),
    "billing.refundChannelReversed": "该订单支付已被支付渠道冲正(拒付),不可申请退款,请联系客服",
    "billing.refundInvoiceIssued": "该订单已开具发票,须先红冲后才能退款,请联系客服",
    "billing.refundNotFound": "退款单不存在",
    "billing.refundOrderNotPaid": "仅支付成功的充值订单可申请退款",
    "billing.refundPayoutSamePerson": (
        "打款登记人与审批人不得为同一人(双人制衡),请由另一位财务操作"
    ),
    "billing.refundStateNotCancellable": "退款单状态 {status} 不可取消",
    "billing.refundStateNotPayable": "退款单状态 {status} 不可登记打款",
    "billing.refundStateNotReviewable": "退款单状态 {status} 不可审批",
    "billing.settlementGapNotFound": "结算缺口不存在",
    "billing.settlementGapNotReplayable": "该缺口类型({reason})不支持重放:请人工核查后核销",
    "billing.settlementGapObjectGone": "缺口关联对象(id={objectId})已不存在,请人工核查后核销",
    "billing.unknownChannel": "未知支付渠道:{name}",
    "billing.wechatCallbackMerchantMismatch": "微信回调的商户信息与本平台不符",
    "billing.wechatCallbackVerifyFailed": "微信回调验签失败",
    "billing.wechatCreateFailed": "微信下单失败:{message}",
    "billing.wechatCredentialsIncomplete": "微信支付商户凭据不完整(管理端·平台配置)",
    "billing.wechatQueryFailed": "微信查单失败:{message}",
    # 商品/镜像
    "catalog.imageRefExists": "镜像 image_ref 已存在",
    "catalog.prewarmDisabled": "该镜像已关闭预热,请先开启",
    "catalog.priceTooSmall": "单价过小:精确到 0.0001 元/时后不能为 0",
    "catalog.priceHourlyTwoDecimals": (
        "按小时计费的规格单价最多 2 位小数(逐小时按 2 位入账,更多位数会产生舍入漂移);"
        "4 位精度仅用于数据盘 GB·月价"
    ),
    "catalog.skuNotSellable": (
        "集群中没有「{model} × {pool} 池」的 Ready 节点,上架后用户将无法开机;确认可强制上架"
    ),
    "catalog.skuNotSellableCpu": (
        "集群中没有「{pool} 池」的 Ready 节点,上架后用户将无法开机;确认可强制上架"
    ),
    "catalog.isolationChangeNeedsOffSale": (
        "在售规格不能改池或 MIG 切片:两者决定隔离方式与用户看到的规格,改了就是另一件商品。"
        "请先下架,或新建规格"
    ),
    "catalog.skuOffSale": "该规格已下架",
    "catalog.tierPoolMismatch": "档位 {tier} 只能落 {pools} 池,当前为 {pool}",
    "catalog.cpuSkuGpuFieldsMustBeZero": (
        "CPU 规格不带卡:GPU 型号须留空,算力份额/显存/单实例卡数须为 0,且不能填 MIG 切片"
    ),
    "catalog.gpuSkuNeedsGpuFields": (
        "GPU 规格必须填 GPU 型号,且算力份额/显存/单实例卡数都不能为 0"
    ),
    "catalog.migProfileMismatch": "mig 池必须填切片规格,其它池必须留空",
    "catalog.skuBusinessKeyExists": (
        "相同型号、档位、池、切片、算力份额与 vCPU/内存的规格已存在,请直接编辑该规格"
    ),
    # 通用兜底
    "common.forbidden": "无权访问",
    "common.httpError": "请求失败({status})",
    "common.internal": "服务器内部错误,请稍后重试",
    "common.methodNotAllowed": "该接口不支持此请求方法",
    "common.networkError": "网络连接失败,请检查网络后重试",
    "common.notFound": "资源不存在",
    "common.payloadTooLarge": "请求体过大,请精简内容后重试",
    "common.auditUnavailable": "审计写入持续失败,写操作已暂时拒绝;请稍后重试或联系平台",
    "common.unauthorized": "未登录或凭证已过期",
    "common.badCursor": "无效的分页游标",
    "common.idempotencyKeyMismatch": (
        "同一幂等键对应了不同的请求参数,已按冲突拒绝:如需新操作请更换幂等键后重试"
    ),
    "common.rateLimited": "尝试过于频繁,请稍后再试",
    "common.retryableConflict": "请求与另一个进行中的操作冲突,请重试",
    "common.validation": "参数校验失败",
    # 数据盘
    "disks.countQuota": "数据盘数量已达上限({max} 块),请删除不用的盘或联系客服提额",
    "disks.realNameRequired": "按监管要求,开通存储前需完成实名认证",
    "disks.expandNeedsActive": "仅正常状态的数据盘可以扩容",
    "disks.inUseDelete": "数据盘挂载中,请先释放对应实例",
    "disks.mountedElsewhere": "数据盘已挂载到其他实例",
    "disks.notMountable": "数据盘当前状态不可挂载",
    "disks.quotaNotSynced": "存储配额同步中,请稍后重试;长时间未恢复请联系客服",
    "disks.shrinkForbidden": "数据盘只支持扩容,不支持缩容",
    "disks.sizeMax": "容量上限 {max} GB",
    "disks.sizeRange": "容量须在 {min}~{max} GB 之间",
    # 法务文档
    "legal.docNotFound": "法务文档不存在或尚未发布",
    "legal.draftExists": "该文档与语言已存在草稿,请先处理现有草稿",
    "legal.versionNotDraft": "版本状态为 {status},仅草稿可执行该操作",
    "legal.publishedNotArchivable": "已发布版本不可直接归档",
    "legal.localeUnsupported": "语言 {locale} 不受支持",
    # 计量/监控
    "metering.badNodeName": "节点名不合法",
    "metering.badRange": "range 须为 1h/6h/24h",
    "metering.unavailable": "监控数据暂不可用,不影响计费(计费依据为实例事件流水)",
    # 节点接入
    "nodes.alreadyTerminal": "状态 {status} 已是终态,无需吊销",
    "nodes.clusterNotReady": "集群调度组件未就绪,暂时无法开机;平台正在自动检测恢复,请稍后重试",
    "nodes.clusterProbeFailed": "集群连接失败:{error}",
    "nodes.clusterNotConfigured": (
        "集群接入参数未配置:请超管在「平台配置 · 集群接入」录入 RKE2 Server 地址与 join token"
    ),
    "nodes.enrollTransition": "注册状态不允许 {from} → {to}",
    "nodes.hostnameMismatch": "主机名与登记不符,令牌已作废,请在管理端核对后重新生成",
    "nodes.nodeNotFound": "节点不在台账中:请确认节点名,或等待下一轮巡检(60 秒)收录后再试",
    "nodes.decommissionDone": (
        "节点已退役:已停止调度、作废该机全部注册令牌,集群侧 Node 对象将在后台删除。"
        "请另行轮换集群 join token 并吊销该机 kubelet 证书"
    ),
    "nodes.regenerateNotAllowed": "状态 {status} 不允许重新生成(仅 待执行/已过期/已失败)",
    "nodes.storageClassMissing": "集群存储未就绪(缺少 {names}),暂时无法开通;请联系平台运维",
    # 实例编排
    "orchestrator.accessNeedsRunning": "实例运行中才能获取接入信息",
    # 网关 extAuth 回调的统一拒绝文案(不区分密钥错 / 已吊销 / 不属端点 / 实例未运行)
    "orchestrator.envKeyInvalid": (
        "环境变量名「{name}」不合法:只能用字母、数字和下划线,且不能以数字开头"
    ),
    "orchestrator.envKeyReserved": (
        "环境变量名「{name}」由平台占用(JUPYTER_ / SUPERDL_ 前缀与 AUTHORIZED_KEYS),请换一个"
    ),
    "orchestrator.envSecretKeyUnknown": "标为密文的环境变量「{name}」不在环境变量列表里",
    "orchestrator.forceStopNeedsRunning": "仅运行中的实例可以强制停止",
    "orchestrator.healthPathSlash": "健康检查路径须以 / 开头",
    "orchestrator.frozenNeedsRecharge": "实例已因欠费冻结,充值解冻后可开机",
    "orchestrator.cpuSkuNoGpu": "该规格为 CPU 实例(不带 GPU),不能选择 GPU 数量",
    "orchestrator.gpuCountRange": "GPU 数量须在 1~{max} 之间",
    "orchestrator.gpuQuota": "GPU 总数将超过上限({max} 卡),请释放后再创建或联系客服提额",
    "orchestrator.imageRefInvalid": "镜像地址格式不正确,示例:registry.example.com/pytorch:2.9",
    "orchestrator.imageRefNotAllowed": "该镜像仓库未被允许,请使用平台镜像或以下仓库:{registries}",
    "orchestrator.imageRefNotPinned": (
        "服务镜像需要指定版本,不能用 latest。请填固定 tag 或 digest,"
        "示例:registry.example.com/vllm:v0.6.3"
    ),
    "orchestrator.instanceQuota": "实例数已达上限({max} 台),请释放后再创建或联系客服提额",
    "orchestrator.invalidTransition": "实例当前状态({from})不允许该操作",
    "orchestrator.logsNeedsRunning": (
        "仅运行中或关机中的实例可读取容器日志:已关机实例无 Pod 日志,请开机后再试"
    ),
    "orchestrator.logsUnavailable": "日志读取失败,请稍后重试",
    "orchestrator.noCapacity": "「{model} × {pool} 池」当前无可分配容量,请稍后重试或选择其他规格",
    "orchestrator.noCapacityCpu": ("「{pool} 池」当前无可分配的 CPU 容量,请稍后重试或选择其他规格"),
    "orchestrator.nodeUnreachable": (
        "实例盘所在节点已失联,暂无法开机;平台处理中,恢复后即可开机。"
        "如长时间未恢复请联系客服(实例盘数据保留在该节点本地盘)"
    ),
    "orchestrator.releaseNeedsStopped": "关机后才能释放实例",
    "orchestrator.realNameRequired": (
        "按监管要求,开通算力前需完成实名认证:请先到「设置 · 实名认证」完成核验"
    ),
    "orchestrator.restartNeedsRunning": "仅运行中的实例可以重启",
    "orchestrator.serviceInstanceLifecycle": (
        "这台实例属于在线服务,请在「在线服务」里停止 / 启动 / 删除该服务"
    ),
    "orchestrator.convertNeedsRunningOrStopped": "只有运行中或已关机的实例可以转包周期",
    "orchestrator.convertNotOnDemand": "只有按量计费的实例可以转包周期",
    "orchestrator.periodNotEnabled": "该规格暂不支持包周期,请选择按量计费",
    "orchestrator.preemptNotSpot": "只有竞价实例可以强制回收",
    "orchestrator.spotNotEnabled": "该规格暂未上竞价档,请选择按量或包周期",
    "orchestrator.toOnDemandNotSpot": "只有竞价实例可以转按量",
    "orchestrator.periodOnOnDemand": "按量计费的实例不能带计费周期",
    "orchestrator.periodRequired": "包周期实例必须选择计费周期",
    "orchestrator.renewNotSubscription": "只有包周期实例可以续费",
    "orchestrator.renewReleased": "实例正在释放或已释放,无法续费",
    "orchestrator.servicePortRequired": "请填写容器监听端口",
    "orchestrator.servicePortReserved": (
        "端口 {port} 由平台占用(22 = SSH,8888 = JupyterLab),请把服务改到其他端口"
    ),
    "orchestrator.sshKeyRequired": "请至少选择一个 SSH 公钥(实例仅支持密钥登录)",
    "orchestrator.sshPortsExhausted": "当前无可分配的 SSH 端口,请稍后重试或联系客服",
    "orchestrator.startNeedsStopped": "仅已关机的实例可以开机",
    "orchestrator.stateChangedRetry": "实例状态已被其他操作变更,请刷新后重试",
    "orchestrator.stopNeedsRunning": "仅运行中的实例可以关机",
    "orchestrator.vcpuQuota": (
        "CPU 实例的 vCPU 总数将超过上限({max} 核),请释放后再创建或联系客服提额"
    ),
    # 在线服务
    "services.apiKeyInvalid": "访问密钥无效",
    "services.apiKeyNotFound": "访问密钥不存在",
    "services.apiKeyQuota": "单个服务的访问密钥已达上限({max} 把),请先吊销不用的密钥",
    "services.deleteNeedsStopped": "请先停止服务,再删除",
    "services.notFound": "服务不存在",
    "services.released": "服务已删除,不能再操作",
    "services.rolloutInFlight": "服务正在更新版本,完成后再试",
    "services.rolloutNeedsSettled": "当前版本正在变更中(部署 / 停止 / 释放),稳定后再更新版本",
    "services.rolloutSubscriptionUnsupported": "包周期服务暂不支持更新版本",
    "services.envKeepUnknown": "要沿用的密文变量在当前版本里不存在:{keys}",
    # 工单
    "tickets.notFound": "工单不存在",
    "tickets.openLimitReached": "进行中的工单已达上限({max} 个),请等待客服处理或关闭后再提交",
    "tickets.stateNotClosable": "工单状态 {status} 不可关闭",
    "tickets.stateNotRepliable": "工单已解决或关闭,不可再回复;如问题未解决请新建工单",
    "tickets.stateNotResolvable": "工单状态 {status} 不可标记解决",
}


def render_message(key: str, params: Mapping[str, Any] | None) -> str:
    """按目录渲染中文文案;缺键/缺参回落并留痕。"""
    template = MESSAGES.get(key)
    if template is None:
        get_logger("app.messages").warning("message_key_missing", key=key)
        return key
    try:
        return template.format(**(params or {}))
    except (KeyError, IndexError):
        get_logger("app.messages").warning("message_params_mismatch", key=key)
        return template
