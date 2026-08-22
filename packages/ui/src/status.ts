/**
 * 状态枚举 → 徽标色 / 文案 key 的单一映射表。
 * 枚举值与后端 status 严格一致,新增状态先改后端再同步这里与 locales 下两语言的 shared.json。
 * labelKey 内嵌 "shared:" 前缀,任意默认 ns 的 t() 均可直接解析;
 * 文案值在 packages/ui/locales/{zh-CN,en-US}/shared.json,键集由 src/locales.test.ts 守护。
 */

import { statusColors } from "./tokens";

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
export type InstanceStatusMeta = (typeof instanceStatusMap)[InstanceStatus];

/** 过渡态(有后台流程在推进):列表/详情页据此决定是否高频轮询。 */
export const TRANSIENT_INSTANCE_STATUSES: readonly string[] = [
  "creating",
  "starting",
  "stopping",
  "releasing",
];

export function isTransientInstanceStatus(status: string): boolean {
  return TRANSIENT_INSTANCE_STATUSES.includes(status);
}

export type SkuTier = "dedicated" | "mig" | "shared_std" | "shared_eco";

export const skuTierMap = {
  dedicated: { labelKey: "shared:status.tier.dedicated", color: "#4F46E5" },
  mig: { labelKey: "shared:status.tier.mig", color: "#0891B2" },
  shared_std: { labelKey: "shared:status.tier.shared_std", color: statusColors.green },
  shared_eco: { labelKey: "shared:status.tier.shared_eco", color: statusColors.orange, hintKey: "shared:status.tierHint.shared_eco" },
} as const satisfies Record<SkuTier, { labelKey: string; color: string; hintKey?: string }>;
export type SkuTierMeta = (typeof skuTierMap)[SkuTier];

/** 镜像节点缓存状态(与 image_node_cache.status 严格一致) */
export type ImageCacheStatus = "pending" | "pulling" | "cached" | "failed";

export const imageCacheStatusMap = {
  pending: { labelKey: "shared:status.imageCache.pending", color: statusColors.gray, badge: "default" },
  pulling: { labelKey: "shared:status.imageCache.pulling", color: statusColors.blue, badge: "processing", animated: true },
  cached: { labelKey: "shared:status.imageCache.cached", color: statusColors.green, badge: "success" },
  failed: { labelKey: "shared:status.imageCache.failed", color: statusColors.red, badge: "error" },
} as const satisfies Record<ImageCacheStatus, StatusMeta>;
export type ImageCacheStatusMeta = (typeof imageCacheStatusMap)[ImageCacheStatus];

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
export type NodeEnrollStatusMeta = (typeof nodeEnrollStatusMap)[NodeEnrollStatus];

export type LedgerType = "recharge" | "consume" | "refund" | "adjust";

export const ledgerTypeMap = {
  recharge: { labelKey: "shared:status.ledger.recharge", color: statusColors.green },
  consume: { labelKey: "shared:status.ledger.consume", color: statusColors.blue },
  refund: { labelKey: "shared:status.ledger.refund", color: statusColors.orange },
  adjust: { labelKey: "shared:status.ledger.adjust", color: "purple" },
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

export type DiskStatus = "active" | "grace" | "frozen" | "deleting" | "deleted";

export const diskStatusMap = {
  active: { labelKey: "shared:status.disk.active", color: statusColors.green, badge: "success" },
  grace: { labelKey: "shared:status.disk.grace", color: statusColors.orange, badge: "warning" },
  frozen: { labelKey: "shared:status.disk.frozen", color: statusColors.orange, badge: "warning" },
  deleting: { labelKey: "shared:status.disk.deleting", color: statusColors.red, badge: "error" },
  deleted: { labelKey: "shared:status.disk.deleted", color: statusColors.gray, badge: "default" },
} as const satisfies Record<DiskStatus, StatusMeta>;
export type DiskStatusMeta = (typeof diskStatusMap)[DiskStatus];
