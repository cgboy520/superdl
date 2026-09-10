/** 控制台主导航(侧栏与窄屏抽屉共用同一份,选中态均为最长前缀匹配)。 */

import {
  ApiOutlined,
  AppstoreOutlined,
  BellOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  HddOutlined,
  QuestionCircleOutlined,
  SettingOutlined,
  WalletOutlined,
} from "@ant-design/icons";
import type { ReactNode } from "react";

export const CONSOLE_NAV = [
  { key: "/dashboard", icon: <DashboardOutlined />, labelKey: "dashboard.title" },
  { key: "/market", icon: <AppstoreOutlined />, labelKey: "market.title" },
  { key: "/instances", icon: <CloudServerOutlined />, labelKey: "instances.title" },
  { key: "/services", icon: <ApiOutlined />, labelKey: "services.title" },
  { key: "/storage", icon: <HddOutlined />, labelKey: "storage.title" },
  { key: "/billing", icon: <WalletOutlined />, labelKey: "billing.title" },
  { key: "/support", icon: <QuestionCircleOutlined />, labelKey: "support.title" },
  { key: "/notifications", icon: <BellOutlined />, labelKey: "notifications.title" },
  { key: "/settings", icon: <SettingOutlined />, labelKey: "settings.title" },
] as const satisfies readonly { key: string; icon: ReactNode; labelKey: string }[];

export function consoleNavSelected(pathname: string): string {
  return (
    CONSOLE_NAV.slice()
      .sort((a, b) => b.key.length - a.key.length)
      .find((n) => pathname.startsWith(n.key))?.key ?? "/dashboard"
  );
}
