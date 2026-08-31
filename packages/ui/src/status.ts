/**
 * 状态枚举 → 徽标色 / 文案 key 的单一映射表。
 * 枚举值与后端 status 严格一致,新增状态先改后端再同步这里与两语言的 shared.json。
 * labelKey 内嵌 "shared:" 前缀,任意默认 ns 的 t() 均可直接解析;键集由 src/locales.test.ts 守护。
 */

import { colorPrimary, statusColors } from "./tokens";

export type InstanceStatus =
  | "creating"
  | "running"
  | "stopping"
  | "stopped"
  | "starting"
  | "frozen"
  | "releasing"
  | "released"
  | "failed";

export interface StatusMeta {
  labelKey: string;
  color: string;
  /** antd Badge status 语义 */
  badge: "success" | "processing" | "default" | "warning" | "error";
  /** 是否显示动效(创建/启动中) */
  animated?: boolean;
}

/** 按运行时字符串安全取表项:保留字面量 labelKey 联合类型,同时补回 undefined 防御。 */
export function metaOf<M extends Record<string, unknown>>(map: M, key: string): M[keyof M] | undefined {
  return (map as Record<string, M[keyof M]>)[key];
}

export const instanceStatusMap = {
  creating: { labelKey: "shared:status.instance.creating", color: statusColors.blue, badge: "processing", animated: true },
  running: { labelKey: "shared:status.instance.running", color: statusColors.green, badge: "success" },
  stopping: { labelKey: "shared:status.instance.stopping", color: statusColors.blue, badge: "processing", animated: true },
  stopped: { labelKey: "shared:status.instance.stopped", color: statusColors.gray, badge: "default" },
  starting: { labelKey: "shared:status.instance.starting", color: statusColors.blue, badge: "processing", animated: true },
  frozen: { labelKey: "shared:status.instance.frozen", color: statusColors.orange, badge: "warning" },
  releasing: { labelKey: "shared:status.instance.releasing", color: statusColors.red, badge: "error", animated: true },
  released: { labelKey: "shared:status.instance.released", color: statusColors.gray, badge: "default" },
  // 中性文案:创建失败与运行中故障共用此状态(精确原因看事件时间线)
  failed: { labelKey: "shared:status.instance.failed", color: statusColors.red, badge: "error" },
} as const satisfies Record<InstanceStatus, StatusMeta>;

/** 过渡态(有后台流程在推进):列表/详情页据此决定是否高频轮询。 */
const TRANSIENT_INSTANCE_STATUSES: readonly string[] = [
  "creating",
  "starting",
  "stopping",
  "releasing",
];

export function isTransientInstanceStatus(status: string): boolean {
  return TRANSIENT_INSTANCE_STATUSES.includes(status);
}

/** 售卖档位,与 skus.tier 严格一致。标准/经济不是档位值,由所在池派生 —— 见 skuVariant。 */
export type SkuTier = "dedicated" | "shared" | "cpu";

/**
 * 用户可见的档位:tier × pool_label 的合并键。隔离机制的事实源是节点池(与后端 core/gpu_adapter 同口径)。
 * 「共享」在 mig 池是硬切分(标准)、在 hami 池是软切分超卖(经济),性能承诺不同必须分开展示;
 * cpu 档不申请 GPU,不按池分化。
 */
export type SkuVariant = "dedicated" | "shared_mig" | "shared_hami" | "cpu";

export function skuVariant(tier: string, poolLabel?: string | null): SkuVariant {
  if (tier === "cpu") return "cpu";
  if (tier === "dedicated") return "dedicated";
  return poolLabel === "mig" ? "shared_mig" : "shared_hami";
}

export const skuTierMap = {
  dedicated: { labelKey: "shared:status.tier.dedicated", color: "#4F46E5" },
  // cyan 档取深:#0891B2 白字仅 3.7:1 不达标;#0E7490 ≈5.4:1(WCAG AA)
  shared_mig: { labelKey: "shared:status.tier.shared_mig", color: "#0E7490", hintKey: "shared:status.tierHint.shared_mig" },
  shared_hami: { labelKey: "shared:status.tier.shared_hami", color: statusColors.orange, hintKey: "shared:status.tierHint.shared_hami" },
  // 灰蓝:与三个 GPU 档的紫/青/橙拉开色相,读作「不带卡」;白字 ≈5.9:1(WCAG AA)
  cpu: { labelKey: "shared:status.tier.cpu", color: "#475569", hintKey: "shared:status.tierHint.cpu" },
} as const satisfies Record<SkuVariant, { labelKey: string; color: string; hintKey?: string }>;

/** 实例形态,与 instances.workload_type 严格一致。dev = SSH + JupyterLab 开发机(默认形态,列表不挂标记),
 *  service = 对外 HTTPS 服务容器(端点 + API Key)。 */
export type WorkloadType = "dev" | "service";

export const workloadTypeMap = {
  dev: { labelKey: "shared:status.workload.dev", color: statusColors.gray },
  // 与档位徽标的紫/青/橙/灰蓝拉开:取品牌靛蓝,白字 ≈7.0:1(WCAG AA)
  service: { labelKey: "shared:status.workload.service", color: colorPrimary },
} as const satisfies Record<WorkloadType, { labelKey: string; color: string }>;

/** 购买模式,与 instances.market 严格一致(后端 core/pricing.py 的 MARKETS)。
 *  与 tier 正交:同一条 SKU 可以按量买也可以包周期买,不是新档位。 */
export type Market = "on_demand" | "spot" | "subscription";

export const marketMap = {
  on_demand: { labelKey: "shared:status.market.on_demand", color: statusColors.gray },
  spot: {
    labelKey: "shared:status.market.spot",
    color: statusColors.orange,
    hintKey: "shared:status.marketHint.spot",
  },
  subscription: { labelKey: "shared:status.market.subscription", color: colorPrimary },
} as const satisfies Record<Market, { labelKey: string; color: string; hintKey?: string }>;

/** 竞价实例的「可回收」行内标记(两端共用),与 `marketMap.spot` 同色。 */
export const spotReclaimTag = {
  labelKey: "shared:status.market.spotReclaimable",
  hintKey: "shared:status.marketHint.spot",
  color: statusColors.orange,
} as const satisfies { labelKey: string; hintKey: string; color: string };

/**
 * 实例事件 `reason` → 文案。值与后端 `transition(reason=...)` 传的字面量严格一致。
 * 后端还有少量自由文本 reason,调用方一律走
 * `metaOf(instanceEventReasonMap, e.reason)?.labelKey ?? e.reason` 以便取不到时原样渲染。
 */
export const instanceEventReasonMap = {
  create: { labelKey: "shared:status.eventReason.create" },
  pod_ready: { labelKey: "shared:status.eventReason.pod_ready" },
  schedule_timeout: { labelKey: "shared:status.eventReason.schedule_timeout" },
  pod_deleted: { labelKey: "shared:status.eventReason.pod_deleted" },
  user_start: { labelKey: "shared:status.eventReason.user_start" },
  user_stop: { labelKey: "shared:status.eventReason.user_stop" },
  restart: { labelKey: "shared:status.eventReason.restart" },
  failed_recover: { labelKey: "shared:status.eventReason.failed_recover" },
  user_release: { labelKey: "shared:status.eventReason.user_release" },
  admin_release: { labelKey: "shared:status.eventReason.admin_release" },
  released: { labelKey: "shared:status.eventReason.released" },
  retention_reclaim: { labelKey: "shared:status.eventReason.retention_reclaim" },
  failed_retention_reclaim: { labelKey: "shared:status.eventReason.failed_retention_reclaim" },
  subscription_renew: { labelKey: "shared:status.eventReason.subscription_renew" },
  subscription_expired: { labelKey: "shared:status.eventReason.subscription_expired" },
  subscription_freeze: { labelKey: "shared:status.eventReason.subscription_freeze" },
  arrears_stop: { labelKey: "shared:status.eventReason.arrears_stop" },
  arrears_freeze: { labelKey: "shared:status.eventReason.arrears_freeze" },
  arrears_reclaim: { labelKey: "shared:status.eventReason.arrears_reclaim" },
  recharge_unfreeze: { labelKey: "shared:status.eventReason.recharge_unfreeze" },
  admin_force_stop: { labelKey: "shared:status.eventReason.admin_force_stop" },
  tenant_frozen: { labelKey: "shared:status.eventReason.tenant_frozen" },
  // 竞价回收(preempt.py 的 REASON_PREEMPTED):自动腾容量与管理端强制回收共用这一条
  preempted: { labelKey: "shared:status.eventReason.preempted" },
} as const satisfies Record<string, { labelKey: string }>;

/** 计费周期,与 subscriptions.period 严格一致(定长小时,见 PERIOD_HOURS)。 */
export type BillingPeriod = "day" | "week" | "month" | "year";

export const BILLING_PERIODS: readonly BillingPeriod[] = ["day", "week", "month", "year"];

export function isBillingPeriod(value: string | null | undefined): value is BillingPeriod {
  return value != null && (BILLING_PERIODS as readonly string[]).includes(value);
}

export const periodMap = {
  day: { labelKey: "shared:status.period.day" },
  week: { labelKey: "shared:status.period.week" },
  month: { labelKey: "shared:status.period.month" },
  year: { labelKey: "shared:status.period.year" },
} as const satisfies Record<BillingPeriod, { labelKey: string }>;

/** 「怎么买的」这一格显示什么:包周期按周期分化成 包日/包周/包月/包年,其余取 market。
 *  两端列表列统一取这里;未知 market 返回 undefined,调用方回退渲染原始值。 */
export function marketLabelKey(market: string, period?: string | null) {
  if (market === "subscription" && isBillingPeriod(period)) return periodMap[period].labelKey;
  return metaOf(marketMap, market)?.labelKey;
}

/** 订阅单状态,与 subscriptions.status 严格一致。 */
export type SubscriptionStatus = "active" | "expired" | "cancelled";

export const subscriptionStatusMap = {
  active: { labelKey: "shared:status.subscription.active", color: statusColors.green, badge: "success" },
  expired: { labelKey: "shared:status.subscription.expired", color: statusColors.orange, badge: "warning" },
  cancelled: { labelKey: "shared:status.subscription.cancelled", color: statusColors.gray, badge: "default" },
} as const satisfies Record<SubscriptionStatus, StatusMeta>;

/** 包周期已到期(开机门禁的前端判据,与后端 subscriptions.assert_active 同口径)。非包周期实例恒为 false;
 *  缺 subscription 字段的包周期实例必须判为已到期(与后端同样 fail-closed)。 */
export function isSubscriptionExpired(
  market: string,
  subscription: { status: string; expires_at: string } | null | undefined,
  now: Date = new Date(),
): boolean {
  if (market !== "subscription") return false;
  if (!subscription) return true;
  return subscription.status !== "active" || new Date(subscription.expires_at).getTime() <= now.getTime();
}

/** 镜像节点缓存状态(与 image_node_cache.status 严格一致) */
export type ImageCacheStatus = "pending" | "pulling" | "cached" | "failed";

export const imageCacheStatusMap = {
  pending: { labelKey: "shared:status.imageCache.pending", color: statusColors.gray, badge: "default" },
  pulling: { labelKey: "shared:status.imageCache.pulling", color: statusColors.blue, badge: "processing", animated: true },
  cached: { labelKey: "shared:status.imageCache.cached", color: statusColors.green, badge: "success" },
  failed: { labelKey: "shared:status.imageCache.failed", color: statusColors.red, badge: "error" },
} as const satisfies Record<ImageCacheStatus, StatusMeta>;

/** 节点注册/加入状态(与 node_enrollments.status 严格一致) */
export type NodeEnrollStatus =
  | "pending"
  | "installing"
  | "rebooting"
  | "joining"
  | "joined"
  | "failed"
  | "expired"
  | "revoked";

export const nodeEnrollStatusMap = {
  pending: { labelKey: "shared:status.nodeEnroll.pending", color: statusColors.gray, badge: "default" },
  installing: { labelKey: "shared:status.nodeEnroll.installing", color: statusColors.blue, badge: "processing", animated: true },
  rebooting: { labelKey: "shared:status.nodeEnroll.rebooting", color: statusColors.blue, badge: "processing", animated: true },
  joining: { labelKey: "shared:status.nodeEnroll.joining", color: statusColors.blue, badge: "processing", animated: true },
  joined: { labelKey: "shared:status.nodeEnroll.joined", color: statusColors.green, badge: "success" },
  failed: { labelKey: "shared:status.nodeEnroll.failed", color: statusColors.red, badge: "error" },
  expired: { labelKey: "shared:status.nodeEnroll.expired", color: statusColors.orange, badge: "warning" },
  revoked: { labelKey: "shared:status.nodeEnroll.revoked", color: statusColors.gray, badge: "default" },
} as const satisfies Record<NodeEnrollStatus, StatusMeta>;

export type LedgerType = "recharge" | "consume" | "refund" | "adjust";

export const ledgerTypeMap = {
  recharge: { labelKey: "shared:status.ledger.recharge", color: statusColors.green },
  consume: { labelKey: "shared:status.ledger.consume", color: statusColors.blue },
  refund: { labelKey: "shared:status.ledger.refund", color: statusColors.orange },
  adjust: { labelKey: "shared:status.ledger.adjust", color: statusColors.purple },
} as const satisfies Record<LedgerType, { labelKey: string; color: string }>;

export type OrderStatus = "pending" | "paid" | "closed" | "failed";

export const orderStatusMap = {
  pending: { labelKey: "shared:status.order.pending", color: statusColors.blue },
  paid: { labelKey: "shared:status.order.paid", color: statusColors.green },
  closed: { labelKey: "shared:status.order.closed", color: statusColors.gray },
  failed: { labelKey: "shared:status.order.failed", color: statusColors.red },
} as const satisfies Record<OrderStatus, { labelKey: string; color: string }>;

export type PaymentChannel = "wechat" | "alipay" | "mock";

export const paymentChannelMap = {
  wechat: { labelKey: "shared:status.channel.wechat" },
  alipay: { labelKey: "shared:status.channel.alipay" },
  mock: { labelKey: "shared:status.channel.mock" },
} as const satisfies Record<PaymentChannel, { labelKey: string }>;

/** 调账单状态(与后端调账单 status 严格一致;管理端财务页用) */
export type AdjustmentStatus = "pending" | "approved" | "rejected";

export const adjustmentStatusMap = {
  pending: { labelKey: "shared:status.adjustment.pending", color: statusColors.blue },
  approved: { labelKey: "shared:status.adjustment.approved", color: statusColors.green },
  rejected: { labelKey: "shared:status.adjustment.rejected", color: statusColors.red },
} as const satisfies Record<AdjustmentStatus, { labelKey: string; color: string }>;

/** 法务文档版本状态(与后端法务文档版本 status 严格一致;管理端法务 Tab 用) */
export type LegalDocStatus = "draft" | "published" | "archived";

export const legalDocStatusMap = {
  draft: { labelKey: "shared:status.legalDoc.draft", color: statusColors.orange },
  published: { labelKey: "shared:status.legalDoc.published", color: statusColors.green },
  archived: { labelKey: "shared:status.legalDoc.archived", color: statusColors.gray },
} as const satisfies Record<LegalDocStatus, { labelKey: string; color: string }>;

/** 退款单状态(与 refund_requests.status 严格一致) */
export type RefundStatus = "pending" | "approved" | "rejected" | "paid" | "cancelled";

export const refundStatusMap = {
  pending: { labelKey: "shared:status.refund.pending", color: statusColors.blue },
  approved: { labelKey: "shared:status.refund.approved", color: statusColors.orange },
  rejected: { labelKey: "shared:status.refund.rejected", color: statusColors.red },
  paid: { labelKey: "shared:status.refund.paid", color: statusColors.green },
  cancelled: { labelKey: "shared:status.refund.cancelled", color: statusColors.gray },
} as const satisfies Record<RefundStatus, { labelKey: string; color: string }>;

/** 退款线下打款渠道(与 schemas.PayoutChannel 严格一致) */
export type PayoutChannel = "offline" | "alipay_transfer" | "wechat_transfer";

export const payoutChannelMap = {
  offline: { labelKey: "shared:status.payoutChannel.offline" },
  alipay_transfer: { labelKey: "shared:status.payoutChannel.alipay_transfer" },
  wechat_transfer: { labelKey: "shared:status.payoutChannel.wechat_transfer" },
} as const satisfies Record<PayoutChannel, { labelKey: string }>;

/** 发票申请状态(与 invoice_requests.status 严格一致) */
export type InvoiceStatus = "submitted" | "issued" | "rejected";

export const invoiceStatusMap = {
  submitted: { labelKey: "shared:status.invoice.submitted", color: statusColors.blue },
  issued: { labelKey: "shared:status.invoice.issued", color: statusColors.green },
  rejected: { labelKey: "shared:status.invoice.rejected", color: statusColors.red },
} as const satisfies Record<InvoiceStatus, { labelKey: string; color: string }>;

/** 工单状态(与 tickets.status 严格一致);用户/管理端共用同一套中性文案 */
export type TicketStatus = "open" | "pending_staff" | "pending_user" | "resolved" | "closed";

export const ticketStatusMap = {
  open: { labelKey: "shared:status.ticket.open", color: statusColors.blue, badge: "processing" },
  pending_staff: { labelKey: "shared:status.ticket.pending_staff", color: statusColors.orange, badge: "warning" },
  pending_user: { labelKey: "shared:status.ticket.pending_user", color: statusColors.orange, badge: "warning" },
  resolved: { labelKey: "shared:status.ticket.resolved", color: statusColors.green, badge: "success" },
  closed: { labelKey: "shared:status.ticket.closed", color: statusColors.gray, badge: "default" },
} as const satisfies Record<TicketStatus, StatusMeta>;

/** resolved/closed 终态不可再回复(服务端同口径 409,前端只是不渲染输入框);用户/管理端共用。 */
const REPLIABLE_TICKET_STATUSES: readonly string[] = ["open", "pending_staff", "pending_user"];

export function isTicketRepliable(status: string): boolean {
  return REPLIABLE_TICKET_STATUSES.includes(status);
}

/** 工单分类(与 tickets.category 严格一致) */
export type TicketCategory = "instance" | "billing" | "data" | "account" | "other";

export const ticketCategoryMap = {
  instance: { labelKey: "shared:status.ticketCategory.instance" },
  billing: { labelKey: "shared:status.ticketCategory.billing" },
  data: { labelKey: "shared:status.ticketCategory.data" },
  account: { labelKey: "shared:status.ticketCategory.account" },
  other: { labelKey: "shared:status.ticketCategory.other" },
} as const satisfies Record<TicketCategory, { labelKey: string }>;

/** 注销申请状态(与 account_deletion_requests.status 严格一致) */
export type DeletionStatus = "pending" | "approved" | "completed" | "rejected" | "cancelled";

export const deletionStatusMap = {
  pending: { labelKey: "shared:status.deletion.pending", color: statusColors.orange, badge: "warning" },
  approved: { labelKey: "shared:status.deletion.approved", color: statusColors.blue, badge: "processing" },
  completed: { labelKey: "shared:status.deletion.completed", color: statusColors.gray, badge: "default" },
  rejected: { labelKey: "shared:status.deletion.rejected", color: statusColors.red, badge: "error" },
  cancelled: { labelKey: "shared:status.deletion.cancelled", color: statusColors.gray, badge: "default" },
} as const satisfies Record<DeletionStatus, StatusMeta>;

export type DiskStatus = "active" | "grace" | "frozen" | "deleting" | "deleted";

export const diskStatusMap = {
  active: { labelKey: "shared:status.disk.active", color: statusColors.green, badge: "success" },
  grace: { labelKey: "shared:status.disk.grace", color: statusColors.orange, badge: "warning" },
  frozen: { labelKey: "shared:status.disk.frozen", color: statusColors.orange, badge: "warning" },
  deleting: { labelKey: "shared:status.disk.deleting", color: statusColors.red, badge: "error" },
  deleted: { labelKey: "shared:status.disk.deleted", color: statusColors.gray, badge: "default" },
} as const satisfies Record<DiskStatus, StatusMeta>;

/** 公告状态(与 announcements.status 严格一致) */
export type AnnouncementStatus = "published" | "revoked";

export const announcementStatusMap = {
  published: { labelKey: "shared:status.announcement.published", color: statusColors.green, badge: "success" },
  revoked: { labelKey: "shared:status.announcement.revoked", color: statusColors.gray, badge: "default" },
} as const satisfies Record<AnnouncementStatus, StatusMeta>;
