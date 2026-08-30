/** 通知中心:全量通知列表 + 已读管理(顶栏 Popover 的完整版)。
 *  全部/未读筛选入 URL(?filter=);行点击标记已读并跳目标页。
 *  行点击行为复用 notificationNav 的 useNotificationOpen(与顶栏 Popover 同一条路径,不分叉)。 */

import { fontSize, formatDateTime } from "@superdl/ui";
import { LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Badge, Button, List, Segmented, Space, theme, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useMarkAllNotificationsRead } from "../api/mutations";
import { useNotificationPages, useUnreadCount } from "../api/queries";
import { useNotificationOpen } from "../components/notificationNav";
import { requireAuth } from "../lib/guard";

type Filter = "all" | "unread";

export const Route = createFileRoute("/_console/notifications")({
  beforeLoad: requireAuth,
  // 筛选入 URL(默认「全部」剥离);非法值丢弃回默认
  validateSearch: (search: Record<string, unknown>): { filter?: Filter } =>
    search.filter === "unread" ? { filter: "unread" } : {},
  component: NotificationsPage,
});

function NotificationsPage() {
  const { t } = useTranslation(["web", "shared"]);
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const { filter } = Route.useSearch();
  const unreadOnly = filter === "unread";
  const pagesQ = useNotificationPages(unreadOnly ? { unread: true } : undefined);
  const items = (pagesQ.data?.pages ?? []).flatMap((p) => p.items);
  const { data: unread } = useUnreadCount();
  const markAllRead = useMarkAllNotificationsRead();
  const open = useNotificationOpen();

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <Typography.Title level={4} style={{ margin: 0 }}>
          {t("notifications.title")}
        </Typography.Title>
        <Space size={12} wrap>
          <Segmented
            value={unreadOnly ? "unread" : "all"}
            onChange={(v) =>
              void navigate({
                to: "/notifications",
                search: v === "unread" ? { filter: "unread" as const } : {},
                replace: true,
              })
            }
            options={[
              { value: "all", label: t("notifications.filterAll") },
              { value: "unread", label: t("notifications.filterUnread") },
            ]}
          />
          <Button
            disabled={!unread || unread.unread_count === 0}
            loading={markAllRead.isPending}
            onClick={() => markAllRead.mutate()}
          >
            {t("topbar.markAllRead")}
          </Button>
        </Space>
      </div>
      <List
        loading={pagesQ.isLoading}
        dataSource={items}
        locale={{
          emptyText: pagesQ.isError ? (
            <TableErrorEmpty isError onRetry={() => void pagesQ.refetch()} />
          ) : (
            <TableErrorEmpty isError={false}>
              {unreadOnly ? t("notifications.emptyUnread") : t("notifications.empty")}
            </TableErrorEmpty>
          ),
        }}
        renderItem={(n) => {
          const isUnread = n.read_at == null;
          return (
            <List.Item
              style={{
                cursor: "pointer",
                // 未读:左色点 + 浅底;已读:普通行
                background: isUnread ? token.colorPrimaryBg : undefined,
                borderInlineStart: isUnread
                  ? `3px solid ${token.colorPrimary}`
                  : "3px solid transparent",
                paddingInline: 12,
              }}
              onClick={() => open(n)}
              // 整行点击必须有键盘语义(同工单列表)
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  open(n);
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
    </Space>
  );
}
