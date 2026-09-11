/** 轮询周期事实源(毫秒)。页面只引用这里,不写裸数字;取值规则见 docs/ui-ux-spec.md §1「轮询三律」。 */
export const POLL = {
  /** 支付单等待回调 */
  payment: 2_000,
  /** 过渡态(creating / starting / stopping / releasing / deploying)逐条轻轮询 */
  transient: 5_000,
  /** 容器日志自动刷新 */
  logs: 10_000,
  /** 工单对话流(进行中) */
  ticket: 15_000,
  /** 角标与稳态详情(未读数 / 告警计数 / 运行中实例详情 / 市场库存) */
  steady: 30_000,
  /** 批量指标摘要(sparkline) */
  metrics: 45_000,
  /** 日消费 / 财务类汇总 */
  daily: 60_000,
} as const;

export type PollKey = keyof typeof POLL;
