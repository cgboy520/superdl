/** Source of truth of the console main navigation (shared by the sidebar / narrow-screen drawer / command palette). Groups: resources (instances / online services / storage) · purchasing & billing (market / billing) · support.
 *  Notifications and account settings are not in the main navigation (reached via the top-bar bell and user menu); the selected state is the longest prefix match, no highlight without a match. */

import {
  ApiOutlined,
  AppstoreOutlined,
  CloudServerOutlined,
  HddOutlined,
  QuestionCircleOutlined,
  WalletOutlined,
} from "@ant-design/icons";

export const CONSOLE_NAV_GROUPS = [
  {
    key: "resources",
    labelKey: "nav.groupResources",
    items: [
      { key: "/instances", icon: <CloudServerOutlined />, labelKey: "instances.title" },
      { key: "/services", icon: <ApiOutlined />, labelKey: "services.title" },
      { key: "/storage", icon: <HddOutlined />, labelKey: "storage.title" },
    ],
  },
  {
    key: "commerce",
    labelKey: "nav.groupCommerce",
    items: [
      { key: "/market", icon: <AppstoreOutlined />, labelKey: "market.title" },
      { key: "/billing", icon: <WalletOutlined />, labelKey: "billing.title" },
    ],
  },
  {
    key: "support",
    labelKey: "nav.groupSupport",
    items: [{ key: "/support", icon: <QuestionCircleOutlined />, labelKey: "support.title" }],
  },
] as const;

export type ConsoleNavGroup = (typeof CONSOLE_NAV_GROUPS)[number];
export type ConsoleNavItem = ConsoleNavGroup["items"][number];

/** Flat items (for the command palette / selected-state matching) */
export const CONSOLE_NAV: readonly ConsoleNavItem[] = CONSOLE_NAV_GROUPS.flatMap(
  (g): readonly ConsoleNavItem[] => g.items,
);

/** Default landing page after login */
export const CONSOLE_HOME = "/instances";

/** Navigation key matched by the current path; undefined (e.g. /settings, /notifications, /help) highlights nothing. */
export function consoleNavSelected(pathname: string): ConsoleNavItem["key"] | undefined {
  return CONSOLE_NAV.slice()
    .sort((a, b) => b.key.length - a.key.length)
    .find((n) => pathname === n.key || pathname.startsWith(`${n.key}/`))?.key;
}
