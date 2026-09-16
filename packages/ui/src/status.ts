/** 状态枚举 → 徽标色 / 文案 key 映射表。枚举值与后端严格一致;labelKey 带 "shared:" 前缀,键集由 locales.test 守护。 */

import { colorPrimary, statusColors } from "./tokens";

export type InstanceStatus =
  "creating" | "running" | "stopping" | "stopped" | "starting" | "frozen" | "releasing" | "released" | "failed";

/** 状态表条目的三种形状:只有文案 / 带颜色 / 带徽标语义(可选图标)。 */
export interface LabelMeta {
  labelKey: string;
  /** Tooltip 解释文案 key */
  hintKey?: string;
}
export interface ColorMeta extends LabelMeta {
  color: string;
}
/** 图标键(由 StatusTag 映射到图标库,本表不依赖图标库) */
export type StatusIcon = "check" | "sync" | "pause" | "warning" | "close" | "clock" | "minus";
export interface StatusMeta extends ColorMeta {
  /** antd Badge status 语义 */
  badge: "success" | "processing" | "default" | "warning" | "error";
  icon?: StatusIcon;
}
export type AnyMeta = LabelMeta | ColorMeta | StatusMeta;
export const isColorMeta = (m: AnyMeta): m is ColorMeta => "color" in m;
export const isStatusMeta = (m: AnyMeta): m is StatusMeta => "badge" in m;

/** 按运行时字符串取表项,未知枚举返回 undefined。 */
export function metaOf<M extends Record<string, unknown>>(map: M, key: string): M[keyof M] | undefined {
  return (map as Record<string, M[keyof M]>)[key];
}

export const instanceStatusMap = {
  creating: {
    labelKey: "shared:status.instance.creating",
    color: statusColors.blue,
    badge: "processing",
    icon: "sync",
  },
  running: { labelKey: "shared:status.instance.running", color: statusColors.green, badge: "success", icon: "check" },
  stopping: {
    labelKey: "shared:status.instance.stopping",
    color: statusColors.blue,
    badge: "processing",
    icon: "sync",
  },
  stopped: { labelKey: "shared:status.instance.stopped", color: statusColors.gray, badge: "default", icon: "pause" },
  starting: {
    labelKey: "shared:status.instance.starting",
    color: statusColors.blue,
    badge: "processing",
    icon: "sync",
  },
  frozen: { labelKey: "shared:status.instance.frozen", color: statusColors.orange, badge: "warning", icon: "warning" },
  releasing: {
    labelKey: "shared:status.instance.releasing",
    color: statusColors.red,
    badge: "error",
    icon: "minus",
  },
  released: { labelKey: "shared:status.instance.released", color: statusColors.gray, badge: "default", icon: "minus" },
  failed: { labelKey: "shared:status.instance.failed", color: statusColors.red, badge: "error", icon: "close" },
} as const satisfies Record<InstanceStatus, StatusMeta>;

/** 过渡态:列表/详情据此高频轮询。 */
const TRANSIENT_INSTANCE_STATUSES: readonly string[] = ["creating", "starting", "stopping", "releasing"];

export function isTransientInstanceStatus(status: string): boolean {
  return TRANSIENT_INSTANCE_STATUSES.includes(status);
}

/** 售卖档位,与 skus.tier 严格一致;标准/经济由所在池派生,见 skuVariant。 */
export type SkuTier = "dedicated" | "shared" | "cpu";

/** 用户可见档位:tier × pool_label(与后端 core/gpu_adapter 同口径)。共享在 mig 池 = 标准,hami 池 = 经济;cpu 不按池分化。 */
export type SkuVariant = "dedicated" | "shared_mig" | "shared_hami" | "cpu";

export function skuVariant(tier: string, poolLabel?: string | null): SkuVariant {
  if (tier === "cpu") return "cpu";
  if (tier === "dedicated") return "dedicated";
  return poolLabel === "mig" ? "shared_mig" : "shared_hami";
}

export const skuTierMap = {
  dedicated: { labelKey: "shared:status.tier.dedicated", color: "#4F46E5" },
  shared_mig: {
    labelKey: "shared:status.tier.shared_mig",
    color: "#0E7490",
    hintKey: "shared:status.tierHint.shared_mig",
  },
  shared_hami: {
    labelKey: "shared:status.tier.shared_hami",
    color: statusColors.orange,
    hintKey: "shared:status.tierHint.shared_hami",
  },
  cpu: { labelKey: "shared:status.tier.cpu", color: "#475569", hintKey: "shared:status.tierHint.cpu" },
} as const satisfies Record<SkuVariant, ColorMeta>;

/** 实例形态,与 instances.workload_type 严格一致:dev = SSH + JupyterLab 开发机(默认,列表不挂标记),service = 对外 HTTPS 服务容器。 */
export type WorkloadType = "dev" | "service";

export const workloadTypeMap = {
  dev: { labelKey: "shared:status.workload.dev", color: statusColors.gray },
  service: { labelKey: "shared:status.workload.service", color: colorPrimary },
} as const satisfies Record<WorkloadType, ColorMeta>;

/** 在线服务派生状态(后端 services/state.py::derive_status,不落库)。unready = 容器在跑但健康检查未过,照常计费;released 译作「已删除」。 */
export type ServiceStatus =
  "deploying" | "running" | "unready" | "stopping" | "stopped" | "frozen" | "failed" | "releasing" | "released";

export const serviceStatusMap = {
  deploying: {
    labelKey: "shared:status.service.deploying",
    color: statusColors.blue,
    badge: "processing",
    icon: "sync",
  },
  running: { labelKey: "shared:status.service.running", color: statusColors.green, badge: "success", icon: "check" },
  unready: {
    labelKey: "shared:status.service.unready",
    color: statusColors.orange,
    badge: "warning",
    icon: "warning",
    hintKey: "shared:status.serviceHint.unready",
  },
  stopping: {
    labelKey: "shared:status.service.stopping",
    color: statusColors.blue,
    badge: "processing",
    icon: "sync",
  },
  stopped: { labelKey: "shared:status.service.stopped", color: statusColors.gray, badge: "default", icon: "pause" },
  frozen: { labelKey: "shared:status.service.frozen", color: statusColors.orange, badge: "warning", icon: "warning" },
  failed: { labelKey: "shared:status.service.failed", color: statusColors.red, badge: "error", icon: "close" },
  releasing: {
    labelKey: "shared:status.service.releasing",
    color: statusColors.red,
    badge: "error",
    icon: "minus",
  },
  released: { labelKey: "shared:status.service.released", color: statusColors.gray, badge: "default", icon: "minus" },
} as const satisfies Record<ServiceStatus, StatusMeta>;

/** 服务过渡态:列表 / 详情据此高频轮询;unready 不算过渡态。 */
const TRANSIENT_SERVICE_STATUSES: readonly string[] = ["deploying", "stopping", "releasing"];

export function isTransientServiceStatus(status: string): boolean {
  return TRANSIENT_SERVICE_STATUSES.includes(status);
}

export function isServiceStatus(value: unknown): value is ServiceStatus {
  return typeof value === "string" && Object.hasOwn(serviceStatusMap, value);
}

/** 列表状态筛选项(不含 released)。 */
export const SERVICE_FILTER_STATUSES: readonly ServiceStatus[] = (
  Object.keys(serviceStatusMap) as ServiceStatus[]
).filter((s) => s !== "released");

/** 购买模式,与 instances.market 严格一致(后端 core/pricing.py MARKET_* 常量);与 tier 正交。 */
export type Market = "on_demand" | "spot" | "subscription";

export const marketMap = {
  on_demand: { labelKey: "shared:status.market.on_demand", color: statusColors.gray },
  spot: {
    labelKey: "shared:status.market.spot",
    color: statusColors.orange,
    hintKey: "shared:status.marketHint.spot",
  },
  subscription: { labelKey: "shared:status.market.subscription", color: colorPrimary },
} as const satisfies Record<Market, ColorMeta>;

/** 竞价实例「可回收」行内标记,与 `marketMap.spot` 同色。 */
export const spotReclaimTag = {
  labelKey: "shared:status.market.spotReclaimable",
  hintKey: "shared:status.marketHint.spot",
  color: statusColors.orange,
} as const satisfies ColorMeta;

/** 实例事件 `reason` → 文案,值与后端 `transition(reason=...)` 字面量一致;自由文本 reason 由调用方经 metaOf 回退原样渲染。 */
export const instanceEventReasonMap = {
  create: { labelKey: "shared:status.eventReason.create" },
  pod_ready: { labelKey: "shared:status.eventReason.pod_ready" },
  schedule_timeout: { labelKey: "shared:status.eventReason.schedule_timeout" },
  pod_deleted: { labelKey: "shared:status.eventReason.pod_deleted" },
  user_start: { labelKey: "shared:status.eventReason.user_start" },
  user_stop: { labelKey: "shared:status.eventReason.user_stop" },
  restart: { labelKey: "shared:status.eventReason.restart" },
  failed_recover: { labelKey: "shared:status.eventReason.failed_recover" },
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
  preempted: { labelKey: "shared:status.eventReason.preempted" },
  rollout: { labelKey: "shared:status.eventReason.rollout" },
  rollout_retire: { labelKey: "shared:status.eventReason.rollout_retire" },
} as const satisfies Record<string, LabelMeta>;

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
} as const satisfies Record<BillingPeriod, LabelMeta>;

/** 购买方式列文案 key:包周期按周期分化成 包日/包周/包月/包年,其余取 market;未知 market 返回 undefined。 */
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

/** 包周期已到期(与后端 subscriptions.assert_active 同口径)。非包周期恒 false;缺 subscription 字段判已到期(fail-closed)。 */
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
  pulling: {
    labelKey: "shared:status.imageCache.pulling",
    color: statusColors.blue,
    badge: "processing",
  },
  cached: { labelKey: "shared:status.imageCache.cached", color: statusColors.green, badge: "success" },
  failed: { labelKey: "shared:status.imageCache.failed", color: statusColors.red, badge: "error" },
} as const satisfies Record<ImageCacheStatus, StatusMeta>;

/** 节点注册/加入状态(与 node_enrollments.status 严格一致) */
export type NodeEnrollStatus =
  "pending" | "installing" | "rebooting" | "joining" | "joined" | "failed" | "expired" | "revoked";

export const nodeEnrollStatusMap = {
  pending: { labelKey: "shared:status.nodeEnroll.pending", color: statusColors.gray, badge: "default" },
  installing: {
    labelKey: "shared:status.nodeEnroll.installing",
    color: statusColors.blue,
    badge: "processing",
  },
  rebooting: {
    labelKey: "shared:status.nodeEnroll.rebooting",
    color: statusColors.blue,
    badge: "processing",
  },
  joining: {
    labelKey: "shared:status.nodeEnroll.joining",
    color: statusColors.blue,
    badge: "processing",
  },
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
} as const satisfies Record<LedgerType, ColorMeta>;

export type OrderStatus = "pending" | "paid" | "closed" | "failed";

export const orderStatusMap = {
  pending: { labelKey: "shared:status.order.pending", color: statusColors.blue, badge: "processing" },
  paid: { labelKey: "shared:status.order.paid", color: statusColors.green, badge: "success" },
  closed: { labelKey: "shared:status.order.closed", color: statusColors.gray, badge: "default" },
  failed: { labelKey: "shared:status.order.failed", color: statusColors.red, badge: "error" },
} as const satisfies Record<OrderStatus, StatusMeta>;

export type PaymentChannel = "wechat" | "alipay" | "stripe" | "mock";

export const paymentChannelMap = {
  wechat: { labelKey: "shared:status.channel.wechat" },
  alipay: { labelKey: "shared:status.channel.alipay" },
  stripe: { labelKey: "shared:status.channel.stripe" },
  mock: { labelKey: "shared:status.channel.mock" },
} as const satisfies Record<PaymentChannel, LabelMeta>;

/** Label key for a channel name coming from the API; unknown names fall back to the raw name. */
export function paymentChannelLabelKey(name: string): string | null {
  return name in paymentChannelMap ? paymentChannelMap[name as PaymentChannel].labelKey : null;
}

/** Refund payout channel that mirrors each paying channel (server: `ChannelSpec.payout_channel`). */
export const payoutChannelForPayment: Partial<Record<string, PayoutChannel>> = {
  wechat: "wechat_transfer",
  alipay: "alipay_transfer",
};

/** 调账单状态(与后端严格一致) */
export type AdjustmentStatus = "pending" | "approved" | "rejected";

export const adjustmentStatusMap = {
  pending: { labelKey: "shared:status.adjustment.pending", color: statusColors.blue, badge: "processing" },
  approved: { labelKey: "shared:status.adjustment.approved", color: statusColors.green, badge: "success" },
  rejected: { labelKey: "shared:status.adjustment.rejected", color: statusColors.red, badge: "error" },
} as const satisfies Record<AdjustmentStatus, StatusMeta>;

/** 法务文档版本状态(与后端严格一致) */
export type LegalDocStatus = "draft" | "published" | "archived";

export const legalDocStatusMap = {
  draft: { labelKey: "shared:status.legalDoc.draft", color: statusColors.orange, badge: "warning" },
  published: { labelKey: "shared:status.legalDoc.published", color: statusColors.green, badge: "success" },
  archived: { labelKey: "shared:status.legalDoc.archived", color: statusColors.gray, badge: "default" },
} as const satisfies Record<LegalDocStatus, StatusMeta>;

/** 退款单状态(与 refund_requests.status 严格一致) */
export type RefundStatus = "pending" | "approved" | "rejected" | "paid" | "cancelled";

export const refundStatusMap = {
  pending: { labelKey: "shared:status.refund.pending", color: statusColors.blue, badge: "processing" },
  approved: { labelKey: "shared:status.refund.approved", color: statusColors.orange, badge: "warning" },
  rejected: { labelKey: "shared:status.refund.rejected", color: statusColors.red, badge: "error" },
  paid: { labelKey: "shared:status.refund.paid", color: statusColors.green, badge: "success" },
  cancelled: { labelKey: "shared:status.refund.cancelled", color: statusColors.gray, badge: "default" },
} as const satisfies Record<RefundStatus, StatusMeta>;

/** 退款线下打款渠道(与 schemas.PayoutChannel 严格一致) */
export type PayoutChannel = "offline" | "alipay_transfer" | "wechat_transfer";

export const payoutChannelMap = {
  offline: { labelKey: "shared:status.payoutChannel.offline" },
  alipay_transfer: { labelKey: "shared:status.payoutChannel.alipay_transfer" },
  wechat_transfer: { labelKey: "shared:status.payoutChannel.wechat_transfer" },
} as const satisfies Record<PayoutChannel, LabelMeta>;

/** 发票申请状态(与 invoice_requests.status 严格一致) */
export type InvoiceStatus = "submitted" | "issued" | "rejected";

export const invoiceStatusMap = {
  submitted: { labelKey: "shared:status.invoice.submitted", color: statusColors.blue, badge: "processing" },
  issued: { labelKey: "shared:status.invoice.issued", color: statusColors.green, badge: "success" },
  rejected: { labelKey: "shared:status.invoice.rejected", color: statusColors.red, badge: "error" },
} as const satisfies Record<InvoiceStatus, StatusMeta>;

/** 工单状态(与 tickets.status 严格一致);两端共用 */
export type TicketStatus = "open" | "pending_staff" | "pending_user" | "resolved" | "closed";

export const ticketStatusMap = {
  open: { labelKey: "shared:status.ticket.open", color: statusColors.blue, badge: "processing" },
  pending_staff: { labelKey: "shared:status.ticket.pending_staff", color: statusColors.orange, badge: "warning" },
  pending_user: { labelKey: "shared:status.ticket.pending_user", color: statusColors.orange, badge: "warning" },
  resolved: { labelKey: "shared:status.ticket.resolved", color: statusColors.green, badge: "success" },
  closed: { labelKey: "shared:status.ticket.closed", color: statusColors.gray, badge: "default" },
} as const satisfies Record<TicketStatus, StatusMeta>;

/** 可回复的工单状态(resolved/closed 不可回复,服务端同口径 409)。 */
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
} as const satisfies Record<TicketCategory, LabelMeta>;

/** 注销申请状态(与 account_deletion_requests.status 严格一致) */
export type DeletionStatus = "pending" | "completed" | "rejected" | "cancelled";

export const deletionStatusMap = {
  pending: { labelKey: "shared:status.deletion.pending", color: statusColors.orange, badge: "warning" },
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

/** 节点状态(与 nodes 台账 status 严格一致,键保后端大小写);?status= 白名单靠 isNodeStatus。 */
export type NodeStatus = "Ready" | "NotReady" | "Cordoned" | "Missing";

export const NODE_STATUSES: readonly NodeStatus[] = ["Ready", "NotReady", "Cordoned", "Missing"];

export function isNodeStatus(value: unknown): value is NodeStatus {
  return typeof value === "string" && (NODE_STATUSES as readonly string[]).includes(value);
}

export const nodeStatusMap = {
  Ready: { labelKey: "shared:status.node.Ready", color: statusColors.green, badge: "success", icon: "check" },
  NotReady: {
    labelKey: "shared:status.node.NotReady",
    color: statusColors.red,
    badge: "error",
    icon: "close",
    hintKey: "shared:status.nodeHint.NotReady",
  },
  Cordoned: {
    labelKey: "shared:status.node.Cordoned",
    color: statusColors.orange,
    badge: "warning",
    icon: "pause",
    hintKey: "shared:status.nodeHint.Cordoned",
  },
  Missing: {
    labelKey: "shared:status.node.Missing",
    color: statusColors.gray,
    badge: "default",
    icon: "minus",
    hintKey: "shared:status.nodeHint.Missing",
  },
} as const satisfies Record<NodeStatus, StatusMeta>;

/** 集群组件体检五态。 */
export type ComponentHealth = "ok" | "degraded" | "down" | "disabled" | "unknown";

export const componentHealthMap = {
  ok: { labelKey: "shared:status.component.ok", color: statusColors.green, badge: "success", icon: "check" },
  degraded: {
    labelKey: "shared:status.component.degraded",
    color: statusColors.orange,
    badge: "warning",
    icon: "warning",
    hintKey: "shared:status.componentHint.degraded",
  },
  down: {
    labelKey: "shared:status.component.down",
    color: statusColors.red,
    badge: "error",
    icon: "close",
  },
  disabled: {
    labelKey: "shared:status.component.disabled",
    color: statusColors.gray,
    badge: "default",
    icon: "minus",
    hintKey: "shared:status.componentHint.disabled",
  },
  unknown: {
    labelKey: "shared:status.component.unknown",
    color: statusColors.gray,
    badge: "default",
    icon: "clock",
    hintKey: "shared:status.componentHint.unknown",
  },
} as const satisfies Record<ComponentHealth, StatusMeta>;

/** 面板排序:要处理的在前,健康的在后。 */
export const COMPONENT_HEALTH_ORDER: readonly ComponentHealth[] = ["down", "degraded", "disabled", "unknown", "ok"];

/** 需要人处理的态:标题计数与横幅按它算。 */
export function isComponentAttention(state: ComponentHealth): boolean {
  return state === "down" || state === "degraded";
}

/** 告警严重度(与 alerts.severity 严格一致);徽标 = 图标 + 文字,不只靠颜色。 */
export type AlertSeverity = "info" | "warning" | "critical";

export const SEVERITY_ORDER: readonly AlertSeverity[] = ["critical", "warning", "info"];

export const severityMap = {
  info: { labelKey: "shared:status.severity.info", color: statusColors.blue, badge: "processing", icon: "clock" },
  warning: {
    labelKey: "shared:status.severity.warning",
    color: statusColors.orange,
    badge: "warning",
    icon: "warning",
  },
  critical: { labelKey: "shared:status.severity.critical", color: statusColors.red, badge: "error", icon: "close" },
} as const satisfies Record<AlertSeverity, StatusMeta>;

/** 告警严重度 → AttentionBar 严重度。 */
export function attentionSeverityOf(s: AlertSeverity): "info" | "warning" | "error" {
  return s === "critical" ? "error" : s;
}

/** 全部状态表(locales.test 遍历它保证 zh/en 键齐全;StatusTag 只接受这里的表)。 */
export const ALL_STATUS_MAPS = [
  instanceStatusMap,
  serviceStatusMap,
  subscriptionStatusMap,
  imageCacheStatusMap,
  nodeEnrollStatusMap,
  nodeStatusMap,
  componentHealthMap,
  severityMap,
  ticketStatusMap,
  deletionStatusMap,
  diskStatusMap,
  announcementStatusMap,
  orderStatusMap,
  adjustmentStatusMap,
  legalDocStatusMap,
  refundStatusMap,
  invoiceStatusMap,
  skuTierMap,
  workloadTypeMap,
  marketMap,
  ledgerTypeMap,
  periodMap,
  paymentChannelMap,
  payoutChannelMap,
  ticketCategoryMap,
  instanceEventReasonMap,
] as const;

export type AnyStatusMap = (typeof ALL_STATUS_MAPS)[number];
