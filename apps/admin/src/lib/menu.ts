/** 侧栏菜单可见性(与后端 require_roles 对齐);key 收窄到 MenuKey。 */

import {
  AlertOutlined,
  ApiOutlined,
  AuditOutlined,
  CloudDownloadOutlined,
  CloudServerOutlined,
  ClusterOutlined,
  CustomerServiceOutlined,
  DeploymentUnitOutlined,
  DashboardOutlined,
  PayCircleOutlined,
  TagsOutlined,
  TeamOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import type { ComponentType } from "react";

export const ALL_ROLES = ["admin", "ops", "finance", "readonly"] as const;

export type Role = (typeof ALL_ROLES)[number];

/** 角色 → 文案键(admin.json roles.*) */
export const ROLE_LABEL_KEY = {
  admin: "roles.admin",
  ops: "roles.ops",
  finance: "roles.finance",
  readonly: "roles.readonly",
} as const satisfies Record<Role, string>;

export const MENU_ROLES = {
  "/": ALL_ROLES,
  "/nodes": ["admin", "ops", "readonly"], // finance 无 /nodes 权限
  "/cluster": ["admin", "ops", "readonly"], // 对齐后端 /cluster require_roles(finance 无)
  "/skus": ALL_ROLES,
  "/images": ["admin", "ops", "readonly"], // 对齐后端 /images require_roles(finance 无)

  "/tenants": ALL_ROLES,
  "/services": ALL_ROLES, // 读全角色;强制停止按钮级 disable + 后端 403 兜底
  "/finance": ["admin", "finance", "readonly"], // ops 无财务权限
  "/tickets": ALL_ROLES, // 读全角色;写操作(ops/admin)由按钮级 disable + 后端 403 兜底
  "/audit": ALL_ROLES,
  "/platform": ["admin"], // 渠道凭据仅超管
  "/alerts": ["admin", "ops", "readonly"], // finance 无告警权限
  "/settings": ALL_ROLES,
} as const satisfies Record<string, readonly Role[]>;

export type MenuKey = keyof typeof MENU_ROLES;

export function canSeeMenu(key: MenuKey, role: string): boolean {
  return (MENU_ROLES[key] as readonly string[]).includes(role);
}

/** 侧栏菜单项:_app.tsx 侧栏与 CommandPalette 共用;icon 存组件引用。 */
export const MENU = [
  { key: "/", icon: DashboardOutlined, labelKey: "menu.overview" },
  { key: "/nodes", icon: ClusterOutlined, labelKey: "menu.nodes" },
  { key: "/cluster", icon: DeploymentUnitOutlined, labelKey: "menu.cluster" },
  { key: "/skus", icon: TagsOutlined, labelKey: "menu.skus" },
  { key: "/images", icon: CloudDownloadOutlined, labelKey: "menu.images" },
  { key: "/tenants", icon: TeamOutlined, labelKey: "menu.tenants" },
  { key: "/services", icon: CloudServerOutlined, labelKey: "menu.services" },
  { key: "/finance", icon: PayCircleOutlined, labelKey: "menu.finance" },
  { key: "/tickets", icon: CustomerServiceOutlined, labelKey: "menu.tickets" },
  { key: "/audit", icon: AuditOutlined, labelKey: "menu.audit" },
  { key: "/platform", icon: ApiOutlined, labelKey: "menu.platform" },
  { key: "/alerts", icon: AlertOutlined, labelKey: "menu.alerts" },
  { key: "/settings", icon: SettingOutlined, labelKey: "menu.settings" },
] as const satisfies readonly { key: MenuKey; icon: ComponentType; labelKey: string }[];
