/** 事件时间线面板(纯展示):顶部常驻「此记录即计费依据」,running 两侧的边标「计费边界」;游标分页。失效由调用方负责;实例详情页与服务详情页共用。 */

import type { InstanceEventOut } from "@superdl/api-client";
import { fontSize, formatDateTime } from "@superdl/ui";
import { DataErrorAlert, LoadMore } from "@superdl/ui/components";
import { Alert, Empty, Space, Timeline, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useEventReasonText } from "../common";

export function EventsPanel({
  events,
  isLoading,
  isError,
  onRetry,
  hasNextPage,
  isFetchingNextPage,
  isFetchNextPageError,
  onLoadMore,
}: {
  events: InstanceEventOut[];
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  isFetchNextPageError: boolean;
  onLoadMore: () => void;
}) {
  const { t } = useTranslation();
  const reasonText = useEventReasonText();
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Alert type="info" showIcon title={t("copy.eventsAreBilling")} />
      {isError && <DataErrorAlert onRetry={onRetry} />}
      {/* 无事件给一句话空态 */}
      {!isError && !isLoading && events.length === 0 && (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("instances.eventsEmpty")} />
      )}
      <Timeline
        items={events.map((e) => ({
          color:
            e.to_status === "running" ? "green" : e.to_status === "failed" ? "red" : "gray",
          content: (
            <Space orientation="vertical" size={0}>
              <Typography.Text strong>
                {e.from_status ?? "—"} → {e.to_status}
                {(e.from_status === "running" || e.to_status === "running") && (
                  <Typography.Text type="secondary">{t("instances.billingBoundary")}</Typography.Text>
                )}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {t("instances.eventMetaLine", {
                  time: formatDateTime(e.created_at),
                  reason: reasonText(e.reason),
                  actor: e.actor,
                })}
              </Typography.Text>
            </Space>
          ),
        }))}
      />
      <LoadMore
        hasNextPage={hasNextPage}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={events.length}
        onLoadMore={onLoadMore}
      />
    </Space>
  );
}
