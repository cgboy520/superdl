/** 侧栏菜单可见性(与后端 require_roles 对齐);key 收窄到 MenuKey。分组(总览 / 资源 / 业务 / 治理)是侧栏与命令面板共用的事实源。 */

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

/** 菜单分组:overview 单项不出组标题;其余三组出标题。 */
export const MENU_GROUP_LABEL_KEY = {
  overview: "menu.groupOverview",
  resources: "menu.groupResources",
  business: "menu.groupBusiness",
  governance: "menu.groupGovernance",
} as const;

export type MenuGroup = keyof typeof MENU_GROUP_LABEL_KEY;

/** 侧栏菜单项:_app.tsx 侧栏与 CommandPalette 共用;icon 存组件引用;group 决定侧栏分组与命令面板分组。 */
export const MENU = [
  { key: "/", icon: DashboardOutlined, labelKey: "menu.overview", group: "overview" },
  { key: "/nodes", icon: ClusterOutlined, labelKey: "menu.nodes", group: "resources" },
  { key: "/cluster", icon: DeploymentUnitOutlined, labelKey: "menu.cluster", group: "resources" },
  { key: "/skus", icon: TagsOutlined, labelKey: "menu.skus", group: "resources" },
  { key: "/images", icon: CloudDownloadOutlined, labelKey: "menu.images", group: "resources" },
  { key: "/tenants", icon: TeamOutlined, labelKey: "menu.tenants", group: "business" },
  { key: "/services", icon: CloudServerOutlined, labelKey: "menu.services", group: "business" },
  { key: "/finance", icon: PayCircleOutlined, labelKey: "menu.finance", group: "business" },
  { key: "/tickets", icon: CustomerServiceOutlined, labelKey: "menu.tickets", group: "business" },
  { key: "/alerts", icon: AlertOutlined, labelKey: "menu.alerts", group: "governance" },
  { key: "/audit", icon: AuditOutlined, labelKey: "menu.audit", group: "governance" },
  { key: "/platform", icon: ApiOutlined, labelKey: "menu.platform", group: "governance" },
  { key: "/settings", icon: SettingOutlined, labelKey: "menu.settings", group: "governance" },
] as const satisfies readonly {
  key: MenuKey;
  icon: ComponentType;
  labelKey: string;
  group: MenuGroup;
}[];

export type MenuItem = (typeof MENU)[number];

/** 分组顺序(侧栏渲染顺序) */
export const MENU_GROUP_ORDER: readonly MenuGroup[] = ["overview", "resources", "business", "governance"];
