/** 事件时间线面板(纯展示):顶部常驻「此记录即计费依据」,running 两侧的边标「计费边界」;游标分页。失效由调用方负责;实例详情页与服务详情页共用。 */

import type { InstanceEventOut } from "@superdl/api-client";
import { fontSize, formatDateTime, instanceStatusMap, metaOf } from "@superdl/ui";
import { DataErrorAlert, LoadMore } from "@superdl/ui/components";
import { Alert, Empty, Space, Tag, Timeline, Typography } from "antd";
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
  const { t } = useTranslation(["web", "shared"]);
  const reasonText = useEventReasonText();
  // 状态枚举走共享映射表翻译,未知值原样回显
  const statusText = (s: string | null | undefined) => {
    if (!s) return "—";
    const meta = metaOf(instanceStatusMap, s);
    return meta ? t(meta.labelKey) : s;
  };
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Alert type="info" showIcon title={t("copy.eventsAreBilling")} />
      {isError && <DataErrorAlert onRetry={onRetry} />}
      {!isError && !isLoading && events.length === 0 && (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("instances.eventsEmpty")} />
      )}
      <Timeline
        items={events.map((e) => ({
          color:
            e.to_status === "running" ? "green" : e.to_status === "failed" ? "red" : "gray",
          content: (
            <Space orientation="vertical" size={0}>
              <Space size={8} align="center">
                <Typography.Text strong>
                  {statusText(e.from_status)} → {statusText(e.to_status)}
                </Typography.Text>
                {(e.from_status === "running" || e.to_status === "running") && (
                  <Tag style={{ marginInlineEnd: 0 }}>{t("instances.billingBoundary")}</Tag>
                )}
              </Space>
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
