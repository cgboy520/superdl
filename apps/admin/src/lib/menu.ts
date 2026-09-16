/** Sidebar menu visibility (aligned with the backend require_roles); keys narrowed to MenuKey. The groups (overview / resources / business / governance) are the source of truth shared by the sidebar and the command palette. */

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

/** Role → locale key (admin.json roles.*) */
export const ROLE_LABEL_KEY = {
  admin: "roles.admin",
  ops: "roles.ops",
  finance: "roles.finance",
  readonly: "roles.readonly",
} as const satisfies Record<Role, string>;

export const MENU_ROLES = {
  "/": ALL_ROLES,
  "/nodes": ["admin", "ops", "readonly"],
  "/cluster": ["admin", "ops", "readonly"],
  "/skus": ALL_ROLES,
  "/images": ["admin", "ops", "readonly"],

  "/tenants": ALL_ROLES,
  "/services": ALL_ROLES,
  "/finance": ["admin", "finance", "readonly"],
  "/tickets": ALL_ROLES,
  "/audit": ALL_ROLES,
  "/platform": ["admin"],
  "/alerts": ["admin", "ops", "readonly"],
  "/settings": ALL_ROLES,
} as const satisfies Record<string, readonly Role[]>;

export type MenuKey = keyof typeof MENU_ROLES;

export function canSeeMenu(key: MenuKey, role: string): boolean {
  return (MENU_ROLES[key] as readonly string[]).includes(role);
}

/** Menu groups: the single overview item has no group title; the other three groups do. */
export const MENU_GROUP_LABEL_KEY = {
  overview: "menu.groupOverview",
  resources: "menu.groupResources",
  business: "menu.groupBusiness",
  governance: "menu.groupGovernance",
} as const;

export type MenuGroup = keyof typeof MENU_GROUP_LABEL_KEY;

/** Sidebar menu items: shared by the _app.tsx sidebar and the CommandPalette; icon holds the component reference; group decides the sidebar and command palette grouping. */
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

/** Group order (sidebar rendering order) */
export const MENU_GROUP_ORDER: readonly MenuGroup[] = ["overview", "resources", "business", "governance"];
