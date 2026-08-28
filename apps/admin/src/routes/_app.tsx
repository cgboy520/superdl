import {
  AlertOutlined,
  ApiOutlined,
  AuditOutlined,
  CloudDownloadOutlined,
  ClusterOutlined,
  CustomerServiceOutlined,
  DeploymentUnitOutlined,
  DashboardOutlined,
  LogoutOutlined,
  MenuOutlined,
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
import { Badge, Button, Dropdown, Layout, Menu, Popover, Space, Tag, Typography, theme } from "antd";
import dayjs from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { type AlertRow, fetchAdminMe, useAlertUnreadCount, useAlerts } from "../api";
import { LangSwitcher } from "../components/LangSwitcher";
import { type MenuKey, ROLE_LABEL_KEY, canSeeMenu } from "../lib/menu";
import { authStore, useAuth } from "../stores/auth";

export const Route = createFileRoute("/_app")({
  beforeLoad: async ({ location }) => {
    const auth = authStore.getState();
    if (!auth.accessToken) {
      throw redirect({ to: "/login", search: { returnTo: location.href } });
    }
    // 角色只信服务端:进入/切换受保护路由都调 /me 校准一次,失效或被撤销的 token 在这里拦下直跳登录。
    // 被降权的账号最迟在下一次路由切换看到新菜单(残余窗口 = 停留在当前页的时长)
    try {
      const me = await fetchAdminMe();
      auth.setAdmin({ id: me.id, username: me.username, role: me.role });
    } catch {
      authStore.getState().logout();
      throw redirect({ to: "/login", search: { returnTo: location.href } });
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
  { key: "/tickets", icon: <CustomerServiceOutlined />, labelKey: "menu.tickets" },
  { key: "/audit", icon: <AuditOutlined />, labelKey: "menu.audit" },
  { key: "/platform", icon: <ApiOutlined />, labelKey: "menu.platform" },
  { key: "/settings", icon: <SettingOutlined />, labelKey: "menu.settings" },
] as const satisfies readonly { key: MenuKey; icon: unknown; labelKey: string }[];

function AlertBell() {
  const { t } = useTranslation();
  const { data } = useAlerts(undefined, { refetchInterval: 30_000 });
  // 角标 = 未确认告警数(独立计数端点,不用当页长度推算)
  const { data: unread } = useAlertUnreadCount({ refetchInterval: 30_000 });
  const alerts: AlertRow[] = data ?? [];
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
                  <Typography.Text style={{ fontSize: 13 }} delete={a.acked_at != null}>
                    {a.title} · {dayjs(a.created_at).format("MM-DD HH:mm")}
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
      <Badge count={unread?.count ?? 0} size="small" title={t("shell.alertsBadgeHint")}>
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
  // 窄屏(lg 断点)Sider 整体收起为 0 宽,Header 出汉堡钮触发展开(antd 标准模式);
  // 断点命中与否由 antd 经 onCollapse 同步进 state,初值给桌面展开态即可
  const [siderCollapsed, setSiderCollapsed] = useState(false);
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
      <Layout.Sider
        width={200}
        breakpoint="lg"
        collapsedWidth={0}
        collapsible
        collapsed={siderCollapsed}
        onCollapse={setSiderCollapsed}
        trigger={null}
        style={{ background: token.colorBgContainer }}
      >
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
        <Menu
          mode="inline"
          selectedKeys={selected}
          items={menuItems}
          style={{ borderRight: 0 }}
          // 窄屏点选即收:Drawer 式覆盖体验,点完不挡内容
          onClick={() => {
            if (window.innerWidth < 992) setSiderCollapsed(true);
          }}
        />
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
          <Space size={12}>
            {/* 窄屏(lg 以下)Sider 已收为 0 宽,菜单入口挪到这里 */}
            <Button
              type="text"
              className="shell-sider-trigger"
              aria-label={t("shell.openMenu")}
              icon={<MenuOutlined />}
              onClick={() => setSiderCollapsed((c) => !c)}
            />
            <Tag color={isProd ? "red" : "cyan"}>{isProd ? t("shell.envProd") : t("shell.envDev")}</Tag>
          </Space>
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
                    const roleKey = metaOf(ROLE_LABEL_KEY, admin?.role ?? "");
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
