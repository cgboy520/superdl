/** 控制台布局:全宽品牌顶栏(56px)压浅色可折叠侧栏(200px,lg 断点收起);market 未登录可看。 */

import {
  AppstoreOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  HddOutlined,
  QuestionCircleOutlined,
  SettingOutlined,
  WalletOutlined,
} from "@ant-design/icons";

import { createFileRoute, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Layout, Menu, theme, Typography } from "antd";

import { AppTopBar } from "../components/layout/AppTopBar";
import { TopBarUser } from "../components/layout/TopBarUser";

export const Route = createFileRoute("/_console")({
  component: ConsoleLayout,
});

// label 复用各页自己的 title 键,避免主导航与页面标题各持一套文案
const NAV = [
  { key: "/dashboard", icon: <DashboardOutlined />, labelKey: "dashboard.title" },
  { key: "/market", icon: <AppstoreOutlined />, labelKey: "market.title" },
  { key: "/instances", icon: <CloudServerOutlined />, labelKey: "instances.title" },
  { key: "/storage", icon: <HddOutlined />, labelKey: "storage.title" },
  { key: "/billing", icon: <WalletOutlined />, labelKey: "billing.title" },
  { key: "/support", icon: <QuestionCircleOutlined />, labelKey: "support.title" },
  { key: "/settings", icon: <SettingOutlined />, labelKey: "settings.title" },
] as const;

function ConsoleLayout() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const selected =
    NAV.slice()
      .sort((a, b) => b.key.length - a.key.length)
      .find((n) => pathname.startsWith(n.key))?.key ?? "/dashboard";

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <AppTopBar variant="console" right={<TopBarUser />} />
      <Layout style={{ flex: 1 }}>
        <Layout.Sider
          width={200}
          theme="light"
          collapsible
          breakpoint="lg"
          collapsedWidth={0}
          style={{ borderRight: `1px solid ${token.colorBorderSecondary}` }}
        >
          <Menu
            mode="inline"
            selectedKeys={[selected]}
            items={NAV.map((n) => ({ key: n.key, icon: n.icon, label: t(n.labelKey) }))}
            onClick={({ key }) => void navigate({ to: key })}
            style={{ borderInlineEnd: "none", paddingTop: 8 }}
          />
        </Layout.Sider>
        <Layout>
          <Layout.Content style={{ padding: 24 }}>
            <div style={{ maxWidth: 1280, margin: "0 auto" }}>
              <Outlet />
            </div>
          </Layout.Content>
          <Layout.Footer style={{ textAlign: "center", paddingBlock: 16 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t("copy.antiMiningNotice")}
            </Typography.Text>
          </Layout.Footer>
        </Layout>
      </Layout>
    </div>
  );
}
