/**
 * 状态枚举 → 徽标色 / 中文名的单一映射表(ui-ux-spec §3.8)。
 * 枚举值与后端 instance status 严格一致,新增状态先改后端再同步这里。
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
  label: string;
  color: string;
  /** antd Badge status 语义 */
  badge: "success" | "processing" | "default" | "warning" | "error";
  /** 是否显示动效(创建/启动中) */
  animated?: boolean;
}

export const instanceStatusMap: Record<InstanceStatus, StatusMeta> = {
  creating: { label: "创建中", color: statusColors.blue, badge: "processing", animated: true },
  running: { label: "运行中", color: statusColors.green, badge: "success" },
  stopping: { label: "关机中", color: statusColors.blue, badge: "processing", animated: true },
  stopped: { label: "已关机", color: statusColors.gray, badge: "default" },
  starting: { label: "启动中", color: statusColors.blue, badge: "processing", animated: true },
  frozen: { label: "已冻结", color: statusColors.orange, badge: "warning" },
  releasing: { label: "释放中", color: statusColors.red, badge: "error", animated: true },
  released: { label: "已释放", color: statusColors.gray, badge: "default" },
  // 中性文案:创建失败与运行中故障共用此状态(精确原因看事件时间线),
  // 徽标不得把运行故障说成「创建失败」
  failed: { label: "已失败", color: statusColors.red, badge: "error" },
};

export type SkuTier = "dedicated" | "mig" | "shared_std" | "shared_eco";

export const skuTierMap: Record<SkuTier, { label: string; color: string; hint?: string }> = {
  dedicated: { label: "独享整卡", color: "#4F46E5" },
  mig: { label: "MIG 切分", color: "#0891B2" },
  shared_std: { label: "共享·标准", color: "#16A34A" },
  shared_eco: { label: "共享·经济", color: "#EA580C", hint: "性能可能波动" },
};

export type DiskStatus = "active" | "grace" | "frozen" | "deleting" | "deleted";

export const diskStatusMap: Record<DiskStatus, StatusMeta> = {
  active: { label: "正常", color: statusColors.green, badge: "success" },
  grace: { label: "宽限期", color: statusColors.orange, badge: "warning" },
  frozen: { label: "已冻结", color: statusColors.orange, badge: "warning" },
  deleting: { label: "清除中", color: statusColors.red, badge: "error" },
  deleted: { label: "已清除", color: statusColors.gray, badge: "default" },
};
