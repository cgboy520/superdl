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
  createFailedRefund: "创建失败,已全额退款,可重新创建或更换规格",

  // 合规
  antiMiningNotice: "严禁将实例用于挖矿等违规用途,违者封号并不予退款(见用户协议)",

  // 安全
  sshKeyOnly: "实例仅支持 SSH 密钥登录,不支持密码登录",
} as const;
