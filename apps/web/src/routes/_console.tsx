/**
 * 控制台布局:全宽品牌顶栏(56px)压浅色可折叠侧栏(200px,lg 断点收起)——
 * AutoDL「顶栏压侧栏」结构 × SuperDL 靛蓝(ui-ux-spec §2)。market 未登录可看。
 */

import {
  AppstoreOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  HddOutlined,
  SettingOutlined,
  WalletOutlined,
} from "@ant-design/icons";
import { copy } from "@superdl/ui";
import { createFileRoute, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { Layout, Menu, theme, Typography } from "antd";

import { AppTopBar } from "../components/layout/AppTopBar";
import { TopBarUser } from "../components/layout/TopBarUser";

export const Route = createFileRoute("/_console")({
  component: ConsoleLayout,
});

const NAV = [
  { key: "/dashboard", icon: <DashboardOutlined />, label: "概览" },
  { key: "/market", icon: <AppstoreOutlined />, label: "算力市场" },
  { key: "/instances", icon: <CloudServerOutlined />, label: "容器实例" },
  { key: "/storage", icon: <HddOutlined />, label: "存储" },
  { key: "/billing", icon: <WalletOutlined />, label: "费用中心" },
  { key: "/settings", icon: <SettingOutlined />, label: "账户设置" },
];

function ConsoleLayout() {
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
          collapsedWidth={64}
          style={{ borderRight: `1px solid ${token.colorBorderSecondary}` }}
        >
          <Menu
            mode="inline"
            selectedKeys={[selected]}
            items={NAV}
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
              {copy.antiMiningNotice}
            </Typography.Text>
          </Layout.Footer>
        </Layout>
      </Layout>
    </div>
  );
}
