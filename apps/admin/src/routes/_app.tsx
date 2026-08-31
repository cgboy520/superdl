import { AlertOutlined, LogoutOutlined, MenuOutlined, SearchOutlined } from "@ant-design/icons";
import { adminLogoutApiAdminV1AuthLogoutPost } from "@superdl/api-client";
import { adminColors, fontSize, formatDateTime, metaOf } from "@superdl/ui";
import { LangSwitcher } from "@superdl/ui/components";
import {
  Link,
  Outlet,
  createFileRoute,
  redirect,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import {
  Badge,
  Button,
  Dropdown,
  Grid,
  Layout,
  Menu,
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
import {
  COMMAND_KBD_HINT,
  COMMAND_PALETTE_OPEN_EVENT,
  CommandPalette,
} from "../components/CommandPalette";
import { alertLink, severityColor, useAckAlertWithFeedback } from "../lib/alertLink";
import { MENU, ROLE_LABEL_KEY, canSeeMenu } from "../lib/menu";
import { queryClient } from "../lib/queryClient";
import { authStore, canWriteOps, useAdminRole, useAuth } from "../stores/auth";

export const Route = createFileRoute("/_app")({
  beforeLoad: async ({ location }) => {
    const auth = authStore.getState();
    if (!auth.accessToken) {
      throw redirect({ to: "/login", search: { returnTo: location.href } });
    }
    // 角色只信服务端:进入/切换受保护路由都调 /me 校准一次,失效或被撤销的 token 在这里拦下直跳登录。
    // 经 queryClient 缓存 15s:连续切换菜单不重复打 /me,角色变化最迟 15s 校准;
    // 被降权的账号最迟在下一次路由切换看到新菜单(残余窗口 = 停留在当前页的时长 + 15s)
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
  // 告警列表只在 Popover 打开时取数(折叠 UI 不空转);
  // 角标 = 未确认告警数(独立计数端点,不用当页长度推算),保留 30s 轮询(角标常显需要)
  const alertsQ = useAlerts(undefined, { enabled: popoverOpen });
  const { data: unread, isError: unreadError } = useAlertUnreadCount({ refetchInterval: 30_000 });
  // 确认闭环同总览告警流/告警中心范式(见 lib/alertLink)
  const ack = useAckAlertWithFeedback();
  const alerts: AlertRow[] = alertsQ.data ?? [];
  return (
    // click 触发 + Button 包裹图标:hover 触发键盘与读屏不可达(web 端通知铃同为此形态)
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
              {alerts.length === 0 && (
                <Typography.Text type="secondary">{t("shell.noAlerts")}</Typography.Text>
              )}
              {alerts.slice(0, 20).map((a) => {
                const link = alertLink(a);
                return (
                  <div
                    key={a.id}
                    style={{ padding: "6px 0", borderBottom: `1px solid ${adminColors.divider}` }}
                  >
                    <Badge
                      color={severityColor(a.severity)}
                      text={
                        <Typography.Text style={{ fontSize: fontSize.body }} delete={a.acked_at != null}>
                          {/* 深链与总览告警流同构;点击后关闭 Popover(受控 open) */}
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
                            loading={ack.isPending && ack.variables?.alertId === a.id}
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
      {/* 计数端点失败时角标显「?」而非静默消失(无权限/故障 ≠ 没有告警) */}
      <Badge count={unreadError ? "?" : (unread?.count ?? 0)} size="small" title={t("shell.alertsBadgeHint")}>
        <Button
          type="text"
          aria-label={t("shell.alertsTitle")}
          icon={
            <AlertOutlined style={{ fontSize: fontSize.sectionTitle, color: adminColors.alertAccent }} />
          }
        />
      </Badge>
    </Popover>
  );
}

/** 命令面板触发器:桌面平铺于顶栏,窄屏收入用户下拉头部(与语言切换/铃铛同策略) */
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

/** 桌面手动收起态的 localStorage 键(只存 trigger 点击;断点自动收展不落盘) */
const SIDER_COLLAPSED_KEY = "superdl.adminSider";

function AppLayout() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const screens = Grid.useBreakpoint();
  const { admin } = useAuth();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  // 收起态拆两份:桌面手动收起(antd 底部 trigger,持久化 localStorage)与窄屏汉堡开合(瞬态);
  // 断点收展由 screens.lg 派生,不落盘——否则断点往返会把「自动收」误存成「手动收」
  const [manualCollapsed, setManualCollapsed] = useState(
    () => localStorage.getItem(SIDER_COLLAPSED_KEY) === "1",
  );
  // 窄屏(lg 断点)Sider 整体收起为 0 宽,Header 出汉堡钮触发展开(antd 标准模式);默认收
  const [mobileOpen, setMobileOpen] = useState(false);
  const siderCollapsed = screens.lg ? manualCollapsed : !mobileOpen;
  const menuItems = MENU.filter((m) => canSeeMenu(m.key, admin?.role ?? "readonly")).map((m) => ({
    key: m.key,
    icon: <m.icon />,
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
        // 桌面手动收 = 80px 图标轨;窄屏收 = 0 宽整体隐藏(菜单入口挪到顶栏汉堡)
        collapsedWidth={screens.lg ? 80 : 0}
        collapsible
        collapsed={siderCollapsed}
        onCollapse={(v, type) => {
          // 只有 trigger 点击算「手动收」并落盘;responsive(断点)由 screens.lg 派生,忽略
          if (type === "clickTrigger") {
            setManualCollapsed(v);
            localStorage.setItem(SIDER_COLLAPSED_KEY, v ? "1" : "0");
          }
        }}
        // 桌面给 antd 默认底部 trigger;窄屏不要 trigger(汉堡钮在 Header)
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
        <Menu
          mode="inline"
          selectedKeys={selected}
          items={menuItems}
          style={{ borderRight: 0 }}
          // 窄屏点选即收:Drawer 式覆盖体验,点完不挡内容
          onClick={() => {
            if (!screens.lg) setMobileOpen(false);
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
            {/* md 以下语言切换/铃铛收入用户下拉(顶栏不溢出);桌面原样平铺 */}
            {screens.md && (
              <>
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
                    onClick: async () => {
                      // 服务端登出(token_version+1,吊销全部在外会话)再清本地态;
                      // 网络失败也照常本地登出(可用性优先,吊销失败仅延迟到 token 过期)
                      try {
                        await adminLogoutApiAdminV1AuthLogoutPost();
                      } catch {
                        /* 登出不受阻 */
                      }
                      authStore.getState().logout();
                      void navigate({ to: "/login" });
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
