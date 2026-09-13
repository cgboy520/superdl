/** 控制台顶栏右区(中性底,图标走 antd token 色):余额入口 · ⌘K · 通知铃 · 主题切换 · 用户菜单(账户设置 / 通知中心 / 帮助 / 语言 / 退出)。
 *  语言与帮助收进用户菜单以精简顶栏;窄屏(<md)只留余额 / 铃 / 用户。未登录(公开市场页)显示登录入口。 */

import { POLL } from "@superdl/ui";
import {
  BellOutlined,
  ExclamationCircleFilled,
  GlobalOutlined,
  LogoutOutlined,
  QuestionCircleOutlined,
  SearchOutlined,
  SettingOutlined,
  UserOutlined,
  WalletOutlined,
} from "@ant-design/icons";
import { flattenPages, fontSize, maskPhone, SUPPORTED_LANGS, useThemeColors } from "@superdl/ui";
import { LoadMore, moneyOr, TableErrorEmpty } from "@superdl/ui/components";
import { Link, useNavigate } from "@tanstack/react-router";
import { Badge, Button, Dropdown, Grid, List, Popover, Space, theme, type MenuProps } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";
import { COMMAND_KBD_HINT, COMMAND_PALETTE_OPEN_EVENT } from "../CommandPalette";
import { NotificationListItem } from "../NotificationListItem";
import { useNotificationOpen } from "../notificationNav";
import { useLogout, useMarkAllNotificationsRead } from "../../api/mutations";
import { useMe, useNotificationPages, useUnreadCount, useWallet } from "../../api/queries";
import { useIsLoggedIn } from "../../stores/auth";
import { ThemeToggle } from "./AppTopBar";

function NotificationBell() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const colors = useThemeColors();
  const countQ = useUnreadCount({ refetchInterval: POLL.steady });
  const pagesQ = useNotificationPages();
  const items = flattenPages(pagesQ.data);
  const unreadCount = countQ.data?.unread_count ?? 0;
  const markAllRead = useMarkAllNotificationsRead();
  const [open, setOpen] = useState(false);
  const openNotification = useNotificationOpen(() => setOpen(false));
  return (
    <Popover
      trigger="click"
      placement="bottomRight"
      open={open}
      onOpenChange={setOpen}
      content={
        pagesQ.isError ? (
          // 失败不渲染成「无通知」
          <div style={{ width: 360 }}>
            <TableErrorEmpty isError onRetry={() => void pagesQ.refetch()} />
          </div>
        ) : (
          <div style={{ width: 360 }}>
            <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 4 }}>
              <Button
                type="link"
                size="small"
                disabled={unreadCount === 0}
                loading={markAllRead.isPending}
                onClick={() => markAllRead.mutate()}
              >
                {t("topbar.markAllRead")}
              </Button>
            </div>
            <List
              style={{ maxHeight: 420, overflow: "auto" }}
              dataSource={items}
              locale={{ emptyText: t("topbar.noNotifications") }}
              renderItem={(n) => <NotificationListItem n={n} onOpen={openNotification} />}
            />
            <LoadMore
              hasNextPage={pagesQ.hasNextPage}
              loading={pagesQ.isFetchingNextPage}
              isError={pagesQ.isFetchNextPageError}
              loadedCount={items.length}
              onLoadMore={() => void pagesQ.fetchNextPage()}
            />
            <div
              style={{
                textAlign: "center",
                borderTop: `1px solid ${token.colorBorderSecondary}`,
                paddingTop: 4,
              }}
            >
              <Link to="/notifications" onClick={() => setOpen(false)}>
                {t("topbar.viewAllNotifications")}
              </Link>
            </div>
          </div>
        )
      }
    >
      {countQ.isError ? (
        <Badge
          size="small"
          count={<ExclamationCircleFilled style={{ color: colors.warning }} />}
          title={t("query.loadFailed")}
        >
          <Button type="text" aria-label={t("topbar.notifications")} icon={<BellOutlined />} />
        </Badge>
      ) : (
        <Badge count={unreadCount} size="small">
          <Button type="text" aria-label={t("topbar.notifications")} icon={<BellOutlined />} />
        </Badge>
      )}
    </Popover>
  );
}

export function TopBarUser() {
  const { t, i18n } = useTranslation(["web", "shared"]);
  const { formatMoney } = useFormat();
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();
  const logout = useLogout();
  const screens = Grid.useBreakpoint();
  const { data: me } = useMe({ enabled: loggedIn });
  const { data: wallet } = useWallet({ enabled: loggedIn });

  if (!loggedIn) {
    return (
      <Button type="primary" onClick={() => void navigate({ to: "/login" })}>
        {t("topbar.loginRegister")}
      </Button>
    );
  }
  const currentLang = i18n.resolvedLanguage === "en-US" ? "en-US" : "zh-CN";
  const langLabel: Record<(typeof SUPPORTED_LANGS)[number], string> = {
    "zh-CN": t("lang.zh", { ns: "shared" }),
    "en-US": t("lang.en", { ns: "shared" }),
  };
  const menuItems: NonNullable<MenuProps["items"]> = [
    { key: "settings", icon: <SettingOutlined />, label: t("settings.title") },
    { key: "notifications", icon: <BellOutlined />, label: t("notifications.title") },
    { key: "help", icon: <QuestionCircleOutlined />, label: t("topbar.help") },
    {
      key: "lang",
      icon: <GlobalOutlined />,
      label: t("lang.switchLabel", { ns: "shared" }),
      children: SUPPORTED_LANGS.map((l) => ({
        key: `lang:${l}`,
        label: langLabel[l],
        disabled: l === currentLang,
      })),
    },
    { type: "divider" },
    { key: "logout", icon: <LogoutOutlined />, label: t("settings.logout"), danger: true },
  ];
  return (
    <Space size={4}>
      <Link to="/billing" className="topbar-balance" aria-label={t("common.balance")}>
        <Space size={4}>
          <WalletOutlined />
          <span style={{ fontWeight: 600 }}>{moneyOr(formatMoney(wallet?.balance ?? "0.00"), wallet != null)}</span>
        </Space>
      </Link>
      {screens.md && (
        <Button
          type="text"
          aria-label={t("command.trigger")}
          title={t("command.trigger")}
          icon={<SearchOutlined />}
          onClick={() => window.dispatchEvent(new CustomEvent(COMMAND_PALETTE_OPEN_EVENT))}
        >
          <span
            className="topbar-kbd-hint"
            style={{
              border: `1px solid ${token.colorBorderSecondary}`,
              borderRadius: 4,
              padding: "0 4px",
              fontSize: fontSize.caption,
              color: token.colorTextSecondary,
            }}
          >
            {COMMAND_KBD_HINT}
          </span>
        </Button>
      )}
      <NotificationBell />
      {screens.md && <ThemeToggle variant="plain" />}
      <Dropdown
        menu={{
          items: menuItems,
          onClick: ({ key }) => {
            if (key === "logout") void logout();
            else if (key === "help") void navigate({ to: "/help" });
            else if (key === "notifications") void navigate({ to: "/notifications" });
            else if (key === "settings") void navigate({ to: "/settings" });
            else if (key.startsWith("lang:")) void i18n.changeLanguage(key.slice(5));
          },
        }}
      >
        <Button type="text" icon={<UserOutlined />} aria-label={t("topbar.userMenu")}>
          {screens.md && me ? maskPhone(me.phone) : ""}
        </Button>
      </Dropdown>
    </Space>
  );
}
