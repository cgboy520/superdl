import {
  AlertOutlined,
  AuditOutlined,
  ClusterOutlined,
  DashboardOutlined,
  LogoutOutlined,
  PayCircleOutlined,
  TagsOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { adminColors } from "@superdl/ui";
import {
  Link,
  Outlet,
  createFileRoute,
  redirect,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import { Badge, Dropdown, Layout, Menu, Popover, Space, Tag, Typography, theme } from "antd";
import dayjs from "dayjs";

import { type AlertRow, useAlerts } from "../api";
import { authStore, useAuth } from "../stores/auth";

export const Route = createFileRoute("/_app")({
  beforeLoad: () => {
    if (!authStore.getState().accessToken) {
      throw redirect({ to: "/login" });
    }
  },
  component: AppLayout,
});

const MENU = [
  { key: "/", icon: <DashboardOutlined />, label: <Link to="/">运营总览</Link> },
  { key: "/nodes", icon: <ClusterOutlined />, label: <Link to="/nodes">节点与 GPU</Link> },
  { key: "/skus", icon: <TagsOutlined />, label: <Link to="/skus">SKU 与定价</Link> },
  { key: "/tenants", icon: <TeamOutlined />, label: <Link to="/tenants">租户与实例</Link> },
  { key: "/finance", icon: <PayCircleOutlined />, label: <Link to="/finance">财务对账</Link> },
  { key: "/audit", icon: <AuditOutlined />, label: <Link to="/audit">审计日志</Link> },
];

const ROLE_LABEL: Record<string, string> = {
  admin: "超级管理员",
  ops: "运维",
  finance: "财务",
  readonly: "只读",
};

function AlertBell() {
  const { data } = useAlerts({ refetchInterval: 30_000 });
  const alerts: AlertRow[] = data ?? [];
  const today = alerts.filter((a) => dayjs(a.created_at).isSame(dayjs(), "day"));
  return (
    <Popover
      placement="bottomRight"
      title="告警流"
      content={
        <div style={{ width: 360, maxHeight: 400, overflow: "auto" }}>
          {alerts.length === 0 && <Typography.Text type="secondary">暂无告警</Typography.Text>}
          {alerts.slice(0, 20).map((a) => (
            <div key={a.id} style={{ padding: "6px 0", borderBottom: "1px solid #1f2a44" }}>
              <Badge
                color={a.severity === "critical" ? "#DC2626" : adminColors.alertAccent}
                text={
                  <Typography.Text style={{ fontSize: 13 }}>
                    {a.title} · {dayjs(a.created_at).format("HH:mm")}
                  </Typography.Text>
                }
              />
              <div style={{ color: "#94A3B8", fontSize: 12, paddingLeft: 14 }}>{a.content}</div>
            </div>
          ))}
        </div>
      }
    >
      <Badge count={today.length} size="small">
        <AlertOutlined style={{ fontSize: 18, color: adminColors.alertAccent, cursor: "pointer" }} />
      </Badge>
    </Popover>
  );
}

function AppLayout() {
  const { token } = theme.useToken();
  const { admin } = useAuth();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const selected = MENU.map((m) => m.key)
    .filter((k) => (k === "/" ? pathname === "/" : pathname.startsWith(k)))
    .slice(-1);
  const isProd = import.meta.env.MODE === "production";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Sider width={200} style={{ background: token.colorBgContainer }}>
        <div
          style={{
            height: 56,
            display: "flex",
            alignItems: "center",
            paddingLeft: 20,
            fontWeight: 700,
            fontSize: 16,
            color: adminColors.dataAccent,
          }}
        >
          SuperDL · NOC
        </div>
        <Menu mode="inline" selectedKeys={selected} items={MENU} style={{ borderRight: 0 }} />
      </Layout.Sider>
      <Layout>
        <Layout.Header
          style={{
            background: token.colorBgContainer,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            paddingInline: 24,
            height: 56,
            lineHeight: "56px",
          }}
        >
          <Tag color={isProd ? "red" : "cyan"}>{isProd ? "生产环境" : "预发/开发"}</Tag>
          <Space size={24}>
            <AlertBell />
            <Dropdown
              menu={{
                items: [
                  {
                    key: "logout",
                    icon: <LogoutOutlined />,
                    label: "退出登录",
                    onClick: () => {
                      authStore.getState().logout();
                      void navigate({ to: "/login" });
                    },
                  },
                ],
              }}
            >
              <Space style={{ cursor: "pointer" }}>
                <Typography.Text>{admin?.username ?? "-"}</Typography.Text>
                <Tag>{ROLE_LABEL[admin?.role ?? ""] ?? admin?.role}</Tag>
              </Space>
            </Dropdown>
          </Space>
        </Layout.Header>
        <Layout.Content style={{ padding: 24 }}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
