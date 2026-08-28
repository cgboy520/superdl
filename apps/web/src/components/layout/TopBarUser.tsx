/** 控制台顶栏右区:余额入口 + 通知铃 + 用户菜单(深底白字适配)。未登录(公开市场页)显示登录入口。 */

import {
  BellOutlined,
  ExclamationCircleFilled,
  LogoutOutlined,
  QuestionCircleOutlined,
  SettingOutlined,
  UserOutlined,
  WalletOutlined,
} from "@ant-design/icons";
import { colorPrimary, formatDateTime, maskPhone } from "@superdl/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { Badge, Button, Dropdown, List, Popover, Space, Typography } from "antd";

import { useTranslation } from "react-i18next";

import { useFormat } from "../../lib/format";
import { moneyOr, TableErrorEmpty } from "../QueryState";
import { useLogout, useMarkAllNotificationsRead, useMarkNotificationRead } from "../../api/mutations";
import { useMe, useNotificationPages, useUnreadCount, useWallet } from "../../api/queries";
import { useIsLoggedIn } from "../../stores/auth";

const WHITE = { color: "#fff" } as const;

function NotificationBell() {
  const { t } = useTranslation();
  // 角标必须走 unread-count 轻端点而非列表长度,否则未读超过一页就不准;弹层列表另走游标分页
  const countQ = useUnreadCount({ refetchInterval: 30_000 });
  const pagesQ = useNotificationPages();
  const items = (pagesQ.data?.pages ?? []).flatMap((p) => p.items);
  const unreadCount = countQ.data?.unread_count ?? 0;
  const markRead = useMarkNotificationRead();
  const markAllRead = useMarkAllNotificationsRead();
  return (
    <Popover
      trigger="click"
      placement="bottomRight"
      content={
        pagesQ.isError ? (
          // 失败绝不渲染成「无通知」
          <div style={{ width: 360 }}>
            <TableErrorEmpty onRetry={() => void pagesQ.refetch()} />
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
            {pagesQ.hasNextPage && (
              <Button
                block
                size="small"
                loading={pagesQ.isFetchingNextPage}
                onClick={() => void pagesQ.fetchNextPage()}
              >
                {t("common.loadMore")}
              </Button>
            )}
          </div>
        )
      }
    >
      {/* 失败态 ≠ 0 角标:查询失败显示告警标记,未读数未知绝不显示 0 */}
      {countQ.isError ? (
        <Badge
          size="small"
          count={<ExclamationCircleFilled style={{ color: "#faad14" }} />}
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
      <NotificationBell />
      {/* 帮助直达:公开 /help 页(FAQ/联系方式) */}
      <Link to="/help" aria-label={t("topbar.help")}>
        <Button type="text" icon={<QuestionCircleOutlined style={WHITE} />} />
      </Link>
      <Dropdown
        menu={{
          items: [
            { key: "settings", icon: <SettingOutlined />, label: t("settings.title") },
            { key: "logout", icon: <LogoutOutlined />, label: t("settings.logout") },
          ],
          onClick: ({ key }) => {
            if (key === "logout") {
              void logout();
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
