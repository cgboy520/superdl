/** 控制台顶栏右区:余额入口 + 通知铃 + 用户菜单(深底白字适配)。未登录(公开市场页)显示登录入口。 */

import {
  BellOutlined,
  ExclamationCircleFilled,
  LogoutOutlined,
  QuestionCircleOutlined,
  SearchOutlined,
  SettingOutlined,
  UserOutlined,
  WalletOutlined,
} from "@ant-design/icons";
import { adminColors, colorPrimary, fontSize, maskPhone } from "@superdl/ui";
import { LoadMore, moneyOr, TableErrorEmpty } from "@superdl/ui/components";
import { Link, useNavigate } from "@tanstack/react-router";
import { Badge, Button, Dropdown, List, Popover, Space, theme } from "antd";
import { useState } from "react";

import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";
import { COMMAND_KBD_HINT, COMMAND_PALETTE_OPEN_EVENT } from "../CommandPalette";
import { NotificationListItem } from "../NotificationListItem";
import { useNotificationOpen } from "../notificationNav";
import { useLogout, useMarkAllNotificationsRead } from "../../api/mutations";
import { useMe, useNotificationPages, useUnreadCount, useWallet } from "../../api/queries";
import { useIsLoggedIn } from "../../stores/auth";

const WHITE = { color: "#fff" } as const;

function NotificationBell() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const countQ = useUnreadCount({ refetchInterval: 30_000 });
  const pagesQ = useNotificationPages();
  const items = (pagesQ.data?.pages ?? []).flatMap((p) => p.items);
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
          // 失败绝不渲染成「无通知」
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
              hasNextPage={pagesQ.hasNextPage ?? false}
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
          count={<ExclamationCircleFilled style={{ color: adminColors.alertAccent }} />}
          title={t("query.loadFailed")}
        >
          <Button type="text" aria-label={t("topbar.notifications")} icon={<BellOutlined style={WHITE} />} />
        </Badge>
      ) : (
        <Badge count={unreadCount} size="small">
          <Button type="text" aria-label={t("topbar.notifications")} icon={<BellOutlined style={WHITE} />} />
        </Badge>
      )}
    </Popover>
  );
}

export function TopBarUser() {
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();
  const logout = useLogout();
  const { data: me } = useMe({ enabled: loggedIn });
  const { data: wallet } = useWallet({ enabled: loggedIn });

  if (!loggedIn) {
    return (
      <Button
        style={{ background: "#fff", color: colorPrimary, borderColor: "transparent", fontWeight: 600 }}
        onClick={() => navigate({ to: "/login" })}
      >
        {t("topbar.loginRegister")}
      </Button>
    );
  }
  return (
    <Space size={12}>
      <Link to="/billing" className="topbar-link">
        <Space size={4}>
          <WalletOutlined />
          <span>{moneyOr(formatMoney(wallet?.balance), wallet != null)}</span>
        </Space>
      </Link>
      <Button
        type="text"
        aria-label={t("command.trigger")}
        title={t("command.trigger")}
        icon={<SearchOutlined style={WHITE} />}
        style={WHITE}
        onClick={() => window.dispatchEvent(new CustomEvent(COMMAND_PALETTE_OPEN_EVENT))}
      >
        <span
          className="topbar-kbd-hint"
          style={{
            border: "1px solid rgba(255,255,255,0.45)",
            borderRadius: 4,
            padding: "0 4px",
            fontSize: fontSize.caption,
          }}
        >
          {COMMAND_KBD_HINT}
        </span>
      </Button>
      <NotificationBell />
      <Link to="/help" aria-label={t("topbar.help")} className="topbar-help-link">
        <Button type="text" icon={<QuestionCircleOutlined style={WHITE} />} />
      </Link>
      <Dropdown
        menu={{
          items: [
            { key: "settings", icon: <SettingOutlined />, label: t("settings.title") },
            { key: "help", icon: <QuestionCircleOutlined />, label: t("topbar.help") },
            { key: "logout", icon: <LogoutOutlined />, label: t("settings.logout") },
          ],
          onClick: ({ key }) => {
            if (key === "logout") {
              void logout();
            } else if (key === "help") {
              void navigate({ to: "/help" });
            } else {
              void navigate({ to: "/settings" });
            }
          },
        }}
      >
        <Button type="text" icon={<UserOutlined style={WHITE} />} style={WHITE}>
          {me ? maskPhone(me.phone) : ""}
        </Button>
      </Dropdown>
    </Space>
  );
}
