import { AlertOutlined, LogoutOutlined, MenuOutlined, SearchOutlined } from "@ant-design/icons";
import { POLL } from "@superdl/ui";
import { adminLogoutApiAdminV1AuthLogoutPost } from "@superdl/api-client";
import { adminColors, fontSize, formatDateTime, layout, metaOf } from "@superdl/ui";
import { LangSwitcher } from "@superdl/ui/components";
import { Link, Outlet, createFileRoute, redirect, useNavigate, useRouterState } from "@tanstack/react-router";
import {
  Badge,
  Button,
  Dropdown,
  Grid,
  Layout,
  Menu,
  type MenuProps,
  Popover,
  Space,
  Tag,
  Tooltip,
  Typography,
  theme,
} from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { type AlertRow, fetchAdminMe, useAlertUnreadCount, useAlerts } from "../api";
import { COMMAND_KBD_HINT, COMMAND_PALETTE_OPEN_EVENT, CommandPalette } from "../components/CommandPalette";
import { alertLink, severityColor, useAckAlertWithFeedback } from "../lib/alertLink";
import { MENU, MENU_GROUP_LABEL_KEY, MENU_GROUP_ORDER, ROLE_LABEL_KEY, canSeeMenu } from "../lib/menu";
import { queryClient } from "../lib/queryClient";
import { authStore, canWriteOps, useAdminRole, useAuth } from "../stores/auth";

export const Route = createFileRoute("/_app")({
  beforeLoad: async ({ location }) => {
    const auth = authStore.getState();
    if (!auth.accessToken) {
      throw redirect({ to: "/login", search: { returnTo: location.href } });
    }
    // 进入受保护路由调 /me 校准角色(缓存 15s),token 失效直跳登录
    try {
      const me = await queryClient.ensureQueryData({
        queryKey: ["admin", "me"],
        queryFn: fetchAdminMe,
        staleTime: 15_000,
      });
      auth.setAdmin({ id: me.id, username: me.username, role: me.role });
    } catch {
      authStore.getState().logout();
      throw redirect({ to: "/login", search: { returnTo: location.href } });
    }
  },
  component: AppLayout,
});

function AlertBell() {
  const { t } = useTranslation(["admin", "shared"]);
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const [popoverOpen, setPopoverOpen] = useState(false);
  // 告警列表只在 Popover 打开时取数;角标 = 未确认告警数(30s 轮询)
  const alertsQ = useAlerts(undefined, { enabled: popoverOpen });
  const { data: unread, isError: unreadError } = useAlertUnreadCount({ refetchInterval: POLL.steady });
  // 确认闭环见 lib/alertLink
  const ack = useAckAlertWithFeedback();
  const alerts: AlertRow[] = alertsQ.data ?? [];
  return (
    // click 触发 + Button 包裹图标(键盘与读屏可达)
    <Popover
      trigger="click"
      placement="bottomRight"
      title={t("shell.alertsTitle")}
      open={popoverOpen}
      onOpenChange={setPopoverOpen}
      content={
        <div style={{ width: 360, maxHeight: 400, overflow: "auto" }}>
          {alertsQ.isError ? (
            <Space orientation="vertical" size={8}>
              <Typography.Text type="secondary">{t("common.loadFailed", { ns: "shared" })}</Typography.Text>
              <Button size="small" onClick={() => void alertsQ.refetch()}>
                {t("common.retry", { ns: "shared" })}
              </Button>
            </Space>
          ) : (
            <>
              {alerts.length === 0 && <Typography.Text type="secondary">{t("shell.noAlerts")}</Typography.Text>}
              {alerts.slice(0, 20).map((a) => {
                const link = alertLink(a);
                return (
                  <div key={a.id} style={{ padding: "6px 0", borderBottom: `1px solid ${adminColors.divider}` }}>
                    <Badge
                      color={severityColor(a.severity)}
                      text={
                        <Typography.Text style={{ fontSize: fontSize.body }} delete={a.acked_at != null}>
                          {link ? (
                            <Link to={link.to} search={link.search} onClick={() => setPopoverOpen(false)}>
                              {a.title}
                            </Link>
                          ) : (
                            a.title
                          )}{" "}
                          · {formatDateTime(a.created_at)}
                        </Typography.Text>
                      }
                    />
                    <div
                      style={{
                        color: adminColors.textSecondary,
                        fontSize: fontSize.caption,
                        paddingLeft: 14,
                      }}
                    >
                      {a.content}
                    </div>
                    {a.acked_at == null && (
                      <div style={{ paddingLeft: 14, marginTop: 2 }}>
                        <Tooltip title={writable ? "" : t("overview.opsOnly")}>
                          <Button
                            size="small"
                            disabled={!writable}
                            loading={ack.isPending && ack.variables.alertId === a.id}
                            onClick={() => ack.mutate({ alertId: a.id })}
                          >
                            {t("overview.ack")}
                          </Button>
                        </Tooltip>
                      </div>
                    )}
                  </div>
                );
              })}
              {alerts.length > 20 && (
                <Typography.Text
                  type="secondary"
                  style={{ display: "block", fontSize: fontSize.caption, paddingTop: 8 }}
                >
                  {t("shell.alertsTruncated")}
                </Typography.Text>
              )}
            </>
          )}
        </div>
      }
    >
      <Badge count={unreadError ? "?" : (unread?.count ?? 0)} size="small" title={t("shell.alertsBadgeHint")}>
        <Button
          type="text"
          aria-label={t("shell.alertsTitle")}
          icon={<AlertOutlined style={{ fontSize: fontSize.sectionTitle, color: adminColors.alertAccent }} />}
        />
      </Badge>
    </Popover>
  );
}

/** 命令面板触发器:桌面平铺顶栏,窄屏收入用户下拉 */
function CommandTrigger() {
  const { t } = useTranslation();
  return (
    <Button
      type="text"
      aria-label={t("command.trigger")}
      title={t("command.trigger")}
      icon={<SearchOutlined />}
      onClick={() => window.dispatchEvent(new CustomEvent(COMMAND_PALETTE_OPEN_EVENT))}
    >
      <Typography.Text
        type="secondary"
        style={{
          fontSize: fontSize.caption,
          border: `1px solid ${adminColors.divider}`,
          borderRadius: 4,
          padding: "0 4px",
        }}
      >
        {COMMAND_KBD_HINT}
      </Typography.Text>
    </Button>
  );
}

/** 桌面手动收起态的 localStorage 键 */
const SIDER_COLLAPSED_KEY = "superdl.adminSider";

function AppLayout() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const screens = Grid.useBreakpoint();
  const { admin } = useAuth();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  // 收起态两份:桌面手动收起(localStorage)与窄屏汉堡开合(瞬态);断点收展由 screens.lg 派生
  const [manualCollapsed, setManualCollapsed] = useState(() => localStorage.getItem(SIDER_COLLAPSED_KEY) === "1");
  // 窄屏 Sider 收为 0 宽,Header 出汉堡钮;默认收
  const [mobileOpen, setMobileOpen] = useState(false);
  const siderCollapsed = screens.lg ? manualCollapsed : !mobileOpen;
  const visible = MENU.filter((m) => canSeeMenu(m.key, admin?.role ?? "readonly"));
  const selected = visible
    .map((m) => m.key)
    .filter((k) => (k === "/" ? pathname === "/" : pathname.startsWith(k)))
    .slice(-1);
  // 分组渲染:overview 单项直出;其余组出组标题(type: group)。展开态 label 是 Link(可中键 / 新标签、带 aria-current);
  // 收起成 80px 图标轨时 label 不可见,由 Menu.onClick 兜底导航
  const menuItems: NonNullable<MenuProps["items"]> = MENU_GROUP_ORDER.flatMap((g) => {
    const items = visible.filter((m) => m.group === g);
    if (items.length === 0) return [];
    const children: NonNullable<MenuProps["items"]> = items.map((m) => ({
      key: m.key,
      icon: <m.icon />,
      label: (
        <Link to={m.key} aria-current={selected[0] === m.key ? "page" : undefined}>
          {t(m.labelKey)}
        </Link>
      ),
    }));
    // 收起态不出组标题(antd 在收起时不渲染 group label,只留缝隙)
    if (g === "overview" || siderCollapsed) return children;
    return [{ key: `group:${g}`, type: "group" as const, label: t(MENU_GROUP_LABEL_KEY[g]), children }];
  });
  const isProd = import.meta.env.MODE === "production";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Sider
        width={200}
        breakpoint="lg"
        // 桌面手动收 = 80px 图标轨;窄屏收 = 0 宽
        collapsedWidth={screens.lg ? 80 : 0}
        collapsible
        collapsed={siderCollapsed}
        onCollapse={(v, type) => {
          // 只有 trigger 点击落盘
          if (type === "clickTrigger") {
            setManualCollapsed(v);
            localStorage.setItem(SIDER_COLLAPSED_KEY, v ? "1" : "0");
          }
        }}
        // 窄屏不要 trigger
        trigger={screens.lg ? undefined : null}
        style={{ background: token.colorBgContainer }}
      >
        <div
          style={{
            height: 56,
            display: "flex",
            alignItems: "center",
            paddingLeft: 20,
            fontWeight: 700,
            fontSize: fontSize.sectionTitle,
            color: adminColors.dataAccent,
          }}
        >
          SuperDL · NOC
        </div>
        <nav aria-label={t("shell.primaryNav")}>
          <Menu
            mode="inline"
            selectedKeys={selected}
            items={menuItems}
            style={{ borderRight: 0 }}
            onClick={({ key }) => {
              // 收起态 label(Link)不可见:点图标由这里导航;展开态 Link 自己处理,避免重复 push
              if (siderCollapsed && screens.lg && typeof key === "string" && key.startsWith("/")) {
                void navigate({ to: key });
              }
              // 窄屏点选即收
              if (!screens.lg) setMobileOpen(false);
            }}
          />
        </nav>
      </Layout.Sider>
      <Layout>
        {/* 顶栏 sticky:表格 sticky 表头以 layout.topBarHeight 为 offset */}
        <Layout.Header
          style={{
            position: "sticky",
            top: 0,
            zIndex: 100,
            background: token.colorBgContainer,
            borderBottom: `1px solid ${adminColors.divider}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            paddingInline: 24,
            height: layout.topBarHeight,
            lineHeight: `${layout.topBarHeight}px`,
          }}
        >
          <Space size={12}>
            {!screens.lg && (
              <Button
                type="text"
                aria-label={t("shell.openMenu")}
                icon={<MenuOutlined />}
                onClick={() => setMobileOpen((o) => !o)}
              />
            )}
            <Tag color={isProd ? "red" : "cyan"}>{isProd ? t("shell.envProd") : t("shell.envDev")}</Tag>
          </Space>
          <Space size={24}>
            {/* md 以下命令面板触发器/语言切换/铃铛收入用户下拉 */}
            {screens.md && (
              <>
                <CommandTrigger />
                <LangSwitcher width={110} />
                <AlertBell />
              </>
            )}
            <Dropdown
              menu={{
                items: [
                  {
                    key: "logout",
                    icon: <LogoutOutlined />,
                    label: t("shell.logout"),
                    onClick: () => {
                      void (async () => {
                        // 服务端登出(吊销全部会话)再清本地态;网络失败也照常本地登出
                        try {
                          await adminLogoutApiAdminV1AuthLogoutPost();
                        } catch {
                          /* 登出不受阻 */
                        }
                        authStore.getState().logout();
                        void navigate({ to: "/login" });
                      })();
                    },
                  },
                ],
              }}
              {...(screens.md
                ? {}
                : {
                    dropdownRender: (menu) => (
                      <div
                        style={{
                          background: token.colorBgElevated,
                          borderRadius: token.borderRadiusLG,
                          boxShadow: token.boxShadowSecondary,
                          overflow: "hidden",
                        }}
                      >
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 12,
                            padding: "8px 12px",
                            borderBottom: `1px solid ${adminColors.divider}`,
                          }}
                        >
                          <CommandTrigger />
                          <LangSwitcher width={110} />
                          <AlertBell />
                        </div>
                        {menu}
                      </div>
                    ),
                  })}
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
        <CommandPalette />
      </Layout>
    </Layout>
  );
}
