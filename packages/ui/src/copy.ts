/**
 * 全站 tooltip / 提示文案集中管理(便于统一修改与后续 i18n)。
 * 红线:所有文案为 SuperDL 原创,不照抄 AutoDL 措辞。
 */

export const copy = {
  // 实例操作禁用原因(条件操作可见但禁用 + tooltip 说明,铁律 #2)
  releaseNeedsStopped: "关机后才能释放实例",
  startNeedsStopped: "仅已关机的实例可以开机",
  stopNeedsRunning: "仅运行中的实例可以关机",
  frozenNeedsRecharge: "实例已因欠费冻结,充值解冻后可操作",
  jupyterNeedsRunning: "实例运行中才能打开 JupyterLab",

  // 计费透明(铁律 #3)
  dailyCostNote: "日常费用:关机也会产生的费用(数据盘按日计费)",
  eventsAreBilling: "此事件记录即计费依据:「运行中」时段按秒累计,精确到关机瞬间",
  monitoringDown: "监控数据暂不可用,不影响计费(计费依据为实例事件流水)",

  // 回收政策透明(超越点 #3)
  freezePolicy: "停止的实例欠费冻结 72 小时后将回收实例盘,数据盘不受影响",
  diskRetention: "数据盘独立于实例,释放实例不丢数据",
  diskExpirePolicy: "数据盘到期后 7 天宽限(只读),再冻结 30 天后清除",

  // 关机/释放确认(铁律 #4)
  stopConfirm: "关机后 GPU 释放,再次开机时若该规格已租完可能需要等待或更换规格",
  releaseConfirmChecklist: "我确认将清除实例盘全部数据(数据盘不受影响)",

  // 经济档知情同意(铁律 #5)
  ecoTierConsent: [
    "共享·经济档为软件隔离的共享算力,算力份额为均值保障",
    "同卡负载高峰时性能可能波动",
    "平台可能在资源紧张时对经济档实例重新调度(会先通知)",
    "价格显著低于标准档,适合容错性高的任务",
  ],

  // 库存与创建(铁律 #1 / #6)
  outOfStock: "已租完",
  stockAvailable: (n: number) => `${n} 卡可租`,
  noCapacityGuide: "当前规格空闲 GPU 不足,试试其他档位或稍后再来",
  createFailedNoCharge: "计费自实例开始运行才开始,本次创建失败未产生任何费用",

  // 合规
  antiMiningNotice: "严禁将实例用于挖矿等违规用途,违者封号并不予退款(见用户协议)",

  // 安全
  sshKeyOnly: "实例仅支持 SSH 密钥登录,不支持密码登录",

  // 预留功能(可见但禁用,铁律 #2;不虚假承诺具体时间)
  comingSoon: "即将上线",
  billingModeComingSoon: "包日/包周/包月计费即将上线,当前仅支持按量计费",
  myImagesComingSoon: "保存镜像功能上线后,可在此选择自己保存的镜像",
  channelComingSoon: "商户资质接入后开放,当前请使用模拟支付(开发环境)",
  realNameComingSoon: "实名认证即将上线",

  // 计费规则说明(市场/创建页「计费规则」链接)
  billingRules: [
    "按量计费:实例「运行中」时段按秒累计,精确到关机瞬间,单价 × 卡数 × 时长",
    "关机即停止 GPU 计费;数据盘按日计费(日常费用),关机也会产生",
    "计费依据为实例事件流水,可在实例详情「事件」页自查",
    "余额不足时实例将被停机,欠费冻结 72 小时后回收实例盘(数据盘不受影响)",
  ],

  // 数据盘行内直建(创建实例页)
  diskCreatedButInstanceFailed:
    "数据盘已创建并开始按日计费;实例创建未成功,可在「存储」页管理或删除该盘",

  // 监控降级(实例列表 sparkline)
  metricsUnavailableShort: "监控暂不可用",
} as const;
