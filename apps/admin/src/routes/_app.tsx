import {
  AlertOutlined,
  ApiOutlined,
  AuditOutlined,
  CloudDownloadOutlined,
  ClusterOutlined,
  DeploymentUnitOutlined,
  DashboardOutlined,
  LogoutOutlined,
  PayCircleOutlined,
  TagsOutlined,
  TeamOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { adminColors, metaOf } from "@superdl/ui";
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
import { useTranslation } from "react-i18next";

import { type AlertRow, useAlerts } from "../api";
import { LangSwitcher } from "../components/LangSwitcher";
import { type MenuKey, canSeeMenu } from "../lib/menu";
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
  { key: "/", icon: <DashboardOutlined />, labelKey: "menu.overview" },
  { key: "/nodes", icon: <ClusterOutlined />, labelKey: "menu.nodes" },
  { key: "/cluster", icon: <DeploymentUnitOutlined />, labelKey: "menu.cluster" },
  { key: "/skus", icon: <TagsOutlined />, labelKey: "menu.skus" },
  { key: "/images", icon: <CloudDownloadOutlined />, labelKey: "menu.images" },
  { key: "/tenants", icon: <TeamOutlined />, labelKey: "menu.tenants" },
  { key: "/finance", icon: <PayCircleOutlined />, labelKey: "menu.finance" },
  { key: "/audit", icon: <AuditOutlined />, labelKey: "menu.audit" },
  { key: "/platform", icon: <ApiOutlined />, labelKey: "menu.platform" },
  { key: "/settings", icon: <SettingOutlined />, labelKey: "menu.settings" },
] as const satisfies readonly { key: MenuKey; icon: unknown; labelKey: string }[];

const ROLE_LABEL = {
  admin: "roles.admin",
  ops: "roles.ops",
  finance: "roles.finance",
  readonly: "roles.readonly",
} as const;

function AlertBell() {
  const { t } = useTranslation();
  const { data } = useAlerts({ refetchInterval: 30_000 });
  const alerts: AlertRow[] = data ?? [];
  const today = alerts.filter((a) => dayjs(a.created_at).isSame(dayjs(), "day"));
  return (
    <Popover
      placement="bottomRight"
      title={t("shell.alertsTitle")}
      content={
        <div style={{ width: 360, maxHeight: 400, overflow: "auto" }}>
          {alerts.length === 0 && <Typography.Text type="secondary">{t("shell.noAlerts")}</Typography.Text>}
          {alerts.slice(0, 20).map((a) => (
            <div
              key={a.id}
              style={{ padding: "6px 0", borderBottom: `1px solid ${adminColors.divider}` }}
            >
              <Badge
                color={a.severity === "critical" ? adminColors.critical : adminColors.alertAccent}
                text={
                  <Typography.Text style={{ fontSize: 13 }}>
                    {a.title} · {dayjs(a.created_at).format("HH:mm")}
                  </Typography.Text>
                }
              />
              <div style={{ color: adminColors.textSecondary, fontSize: 12, paddingLeft: 14 }}>
                {a.content}
              </div>
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
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const { admin } = useAuth();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const menuItems = MENU.filter((m) => canSeeMenu(m.key, admin?.role ?? "readonly")).map((m) => ({
    key: m.key,
    icon: m.icon,
    label: <Link to={m.key}>{t(m.labelKey)}</Link>,
  }));
  const selected = menuItems
    .map((m) => m.key)
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
        <Menu mode="inline" selectedKeys={selected} items={menuItems} style={{ borderRight: 0 }} />
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
          <Tag color={isProd ? "red" : "cyan"}>{isProd ? t("shell.envProd") : t("shell.envDev")}</Tag>
          <Space size={24}>
            <LangSwitcher />
            <AlertBell />
            <Dropdown
              menu={{
                items: [
                  {
                    key: "logout",
                    icon: <LogoutOutlined />,
                    label: t("shell.logout"),
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
                <Tag>
                  {(() => {
                    const roleKey = metaOf(ROLE_LABEL, admin?.role ?? "");
                    return roleKey ? t(roleKey) : admin?.role;
                  })()}
                </Tag>
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
