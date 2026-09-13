/** 通知中心(PageContainer narrow):全量通知列表 + 已读管理。全部/未读筛选入 URL(?filter=);行点击复用 notificationNav 的 useNotificationOpen。 */

import { flattenPages, space } from "@superdl/ui";
import { EmptyState, LoadMore, PageContainer, TableErrorEmpty } from "@superdl/ui/components";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Button, List, Segmented, Space } from "antd";
import { useTranslation } from "react-i18next";

import { useMarkAllNotificationsRead } from "../api/mutations";
import { useNotificationPages, useUnreadCount } from "../api/queries";
import { NotificationListItem } from "../components/NotificationListItem";
import { useNotificationOpen } from "../components/notificationNav";
import { requireAuth } from "../lib/guard";

type Filter = "all" | "unread";

export const Route = createFileRoute("/_console/notifications")({
  beforeLoad: requireAuth,
  // 筛选入 URL(默认「全部」剥离);非法值回默认
  validateSearch: (search: Record<string, unknown>): { filter?: Filter } =>
    search.filter === "unread" ? { filter: "unread" } : {},
  component: NotificationsPage,
});

function NotificationsPage() {
  const { t } = useTranslation(["web", "shared"]);
  const navigate = useNavigate();
  const { filter } = Route.useSearch();
  const unreadOnly = filter === "unread";
  const pagesQ = useNotificationPages(unreadOnly ? { unread: true } : undefined);
  const items = flattenPages(pagesQ.data);
  const { data: unread } = useUnreadCount();
  const markAllRead = useMarkAllNotificationsRead();
  const open = useNotificationOpen();

  return (
    <PageContainer
      width="narrow"
      title={t("notifications.title")}
      extra={
        <Button
          disabled={!unread || unread.unread_count === 0}
          loading={markAllRead.isPending}
          onClick={() => markAllRead.mutate()}
        >
          {t("topbar.markAllRead")}
        </Button>
      }
    >
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
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
        <List
          loading={pagesQ.isLoading}
          dataSource={items}
          locale={{
            emptyText: pagesQ.isError ? (
              <TableErrorEmpty isError onRetry={() => void pagesQ.refetch()} />
            ) : (
              <EmptyState
                scene="notification"
                description={unreadOnly ? t("notifications.emptyUnread") : t("notifications.empty")}
              />
            ),
          }}
          renderItem={(n) => <NotificationListItem n={n} onOpen={open} />}
        />
        <LoadMore
          hasNextPage={pagesQ.hasNextPage}
          loading={pagesQ.isFetchingNextPage}
          isError={pagesQ.isFetchNextPageError}
          loadedCount={items.length}
          onLoadMore={() => void pagesQ.fetchNextPage()}
        />
      </Space>
    </PageContainer>
  );
}
