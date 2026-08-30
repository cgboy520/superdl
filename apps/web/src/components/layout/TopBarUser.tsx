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
import { adminColors, colorPrimary, fontSize, formatDateTime, maskPhone } from "@superdl/ui";
import { LoadMore, moneyOr, TableErrorEmpty } from "@superdl/ui/components";
import { Link, useNavigate } from "@tanstack/react-router";
import { Badge, Button, Dropdown, List, Popover, Space, theme, Typography } from "antd";
import { useState } from "react";

import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";
import { COMMAND_KBD_HINT, COMMAND_PALETTE_OPEN_EVENT } from "../CommandPalette";
import { useNotificationOpen } from "../notificationNav";
import { useLogout, useMarkAllNotificationsRead } from "../../api/mutations";
import { useMe, useNotificationPages, useUnreadCount, useWallet } from "../../api/queries";
import { useIsLoggedIn } from "../../stores/auth";

const WHITE = { color: "#fff" } as const;

function NotificationBell() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  // 角标必须走 unread-count 轻端点而非列表长度,否则未读超过一页就不准;弹层列表另走游标分页
  const countQ = useUnreadCount({ refetchInterval: 30_000 });
  const pagesQ = useNotificationPages();
  const items = (pagesQ.data?.pages ?? []).flatMap((p) => p.items);
  const unreadCount = countQ.data?.unread_count ?? 0;
  const markAllRead = useMarkAllNotificationsRead();
  // 条目点击行为与通知中心同一条路径(标已读+深链);跳转后关闭 Popover
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
              renderItem={(n) => {
                const isUnread = n.read_at == null;
                return (
                  <List.Item
                    style={{
                      cursor: "pointer",
                      // 已读/未读视觉与通知中心同语言:未读 = 左色点 + 浅底 + 左边框
                      background: isUnread ? token.colorPrimaryBg : undefined,
                      borderInlineStart: isUnread
                        ? `3px solid ${token.colorPrimary}`
                        : "3px solid transparent",
                      paddingInline: 12,
                    }}
                    onClick={() => openNotification(n)}
                    // 整行点击必须有键盘语义(与通知中心/工单列表同一标准)
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        openNotification(n);
                      }
                    }}
                  >
                    <List.Item.Meta
                      title={
                        <Space size={8}>
                          {isUnread && <Badge color={token.colorPrimary} />}
                          <Typography.Text strong={isUnread}>{n.title}</Typography.Text>
                        </Space>
                      }
                      description={
                        <>
                          <div>{n.content}</div>
                          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                            {formatDateTime(n.created_at)}
                          </Typography.Text>
                        </>
                      }
                    />
                  </List.Item>
                );
              }}
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
      {/* 失败态 ≠ 0 角标:查询失败显示告警标记,未读数未知绝不显示 0;
          告警色走 token(深底品牌顶栏上 alertAccent 才够亮,antd 默认 #faad14 非 token 不硬编码) */}
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
          {/* 未就绪必须显示 —,不能渲染假 ¥0.00:查询失败时 data 恒为 undefined */}
          <span>{moneyOr(formatMoney(wallet?.balance), wallet != null)}</span>
        </Space>
      </Link>
      {/* Cmd+K 命令面板触发器(全局快捷键在 CommandPalette 内监听);kbd 提示徽章窄屏隐藏(右区防溢出) */}
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
      {/* 帮助直达:公开 /help 页(FAQ/联系方式);窄屏收进用户菜单(右区五件防溢出) */}
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
