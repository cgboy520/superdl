/** 控制台布局:左 200px 固定导航 + 顶栏(余额/通知铃/用户菜单)。market 未登录可看。 */

import {
  AppstoreOutlined,
  BellOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  HddOutlined,
  LogoutOutlined,
  SettingOutlined,
  UserOutlined,
  WalletOutlined,
} from "@ant-design/icons";
import { colorPrimary, copy, formatDateTime, formatMoney } from "@superdl/ui";
import {
  createFileRoute,
  Link,
  Outlet,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import { Badge, Button, Dropdown, Layout, List, Menu, Popover, Space, Typography } from "antd";

import { useMarkNotificationRead } from "../api/mutations";
import { useMe, useNotifications, useWallet } from "../api/queries";
import { useIsLoggedIn, authStore } from "../stores/auth";

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

function NotificationBell() {
  const { data: unread } = useNotifications({ unread: true }, { refetchInterval: 30_000 });
  const { data: all } = useNotifications({});
  const markRead = useMarkNotificationRead();
  return (
    <Popover
      trigger="click"
      placement="bottomRight"
      content={
        <List
          style={{ width: 360, maxHeight: 420, overflow: "auto" }}
          dataSource={all ?? []}
          locale={{ emptyText: "暂无通知" }}
          renderItem={(n) => (
            <List.Item
              style={{ opacity: n.read_at ? 0.55 : 1, cursor: n.read_at ? undefined : "pointer" }}
              onClick={() => {
                if (!n.read_at) markRead.mutate(n.id);
              }}
            >
              <List.Item.Meta
                title={n.title}
                description={
                  <>
                    <div>{n.content}</div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {formatDateTime(n.created_at)}
                    </Typography.Text>
                  </>
                }
              />
            </List.Item>
          )}
        />
      }
    >
      <Badge count={unread?.length ?? 0} size="small">
        <Button type="text" icon={<BellOutlined />} />
      </Badge>
    </Popover>
  );
}

function TopBarUser() {
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();
  const { data: me } = useMe({ enabled: loggedIn });
  const { data: wallet } = useWallet({ enabled: loggedIn });

  if (!loggedIn) {
    return (
      <Button type="primary" onClick={() => navigate({ to: "/login" })}>
        登录 / 注册
      </Button>
    );
  }
  return (
    <Space size={16}>
      <Link to="/billing">
        <Space size={4}>
          <WalletOutlined />
          <span>{formatMoney(wallet?.balance)}</span>
        </Space>
      </Link>
      <NotificationBell />
      <Dropdown
        menu={{
          items: [
            { key: "settings", icon: <SettingOutlined />, label: "账户设置" },
            { key: "logout", icon: <LogoutOutlined />, label: "退出登录" },
          ],
          onClick: ({ key }) => {
            if (key === "logout") {
              authStore.getState().logout();
              void navigate({ to: "/login" });
            } else {
              void navigate({ to: "/settings" });
            }
          },
        }}
      >
        <Button type="text" icon={<UserOutlined />}>
          {me?.phone}
        </Button>
      </Dropdown>
    </Space>
  );
}

function ConsoleLayout() {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const selected =
    NAV.slice()
      .sort((a, b) => b.key.length - a.key.length)
      .find((n) => pathname.startsWith(n.key))?.key ?? "/dashboard";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Sider width={200} theme="light" style={{ borderRight: "1px solid #f0f0f0" }}>
        <div
          style={{
            height: 56,
            display: "flex",
            alignItems: "center",
            paddingLeft: 24,
            fontSize: 18,
            fontWeight: 700,
            color: colorPrimary,
          }}
        >
          SuperDL
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          items={NAV}
          onClick={({ key }) => void navigate({ to: key })}
          style={{ borderInlineEnd: "none" }}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header
          style={{
            background: "#fff",
            borderBottom: "1px solid #f0f0f0",
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            paddingInline: 24,
            height: 56,
            lineHeight: "56px",
          }}
        >
          <TopBarUser />
        </Layout.Header>
        <Layout.Content style={{ padding: 24, maxWidth: 1280, width: "100%", margin: "0 auto" }}>
          <Outlet />
        </Layout.Content>
        <Layout.Footer style={{ textAlign: "center", color: "#999", fontSize: 12 }}>
          {copy.antiMiningNotice}
        </Layout.Footer>
      </Layout>
    </Layout>
  );
}
