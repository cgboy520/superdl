/** 控制台主导航事实源(侧栏 / 窄屏抽屉 / 命令面板共用)。分组:资源(实例 / 在线服务 / 存储)· 购买与账务(算力市场 / 费用中心)· 支持。
 *  通知与账户设置不在主导航(经顶栏铃铛与用户菜单到达);选中态为最长前缀匹配,不命中则无高亮。 */

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

/** 扁平项(命令面板 / 选中态匹配用) */
export const CONSOLE_NAV: readonly ConsoleNavItem[] = CONSOLE_NAV_GROUPS.flatMap(
  (g): readonly ConsoleNavItem[] => g.items,
);

/** 登录后默认落地页 */
export const CONSOLE_HOME = "/instances";

/** 当前路径命中的导航 key;不命中(如 /settings、/notifications、/help)返回 undefined,不高亮任何项。 */
export function consoleNavSelected(pathname: string): ConsoleNavItem["key"] | undefined {
  return CONSOLE_NAV.slice()
    .sort((a, b) => b.key.length - a.key.length)
    .find((n) => pathname === n.key || pathname.startsWith(`${n.key}/`))?.key;
}
