/** 事件时间线面板(纯展示):「此记录即计费依据」放标题旁 tooltip 不做常驻条;running 两侧的边标「计费边界」;可只看计费边界 / 只看失败;游标分页。
 *  失效由调用方负责;实例详情页与服务详情页共用。 */

import { QuestionCircleOutlined } from "@ant-design/icons";
import type { InstanceEventOut } from "@superdl/api-client";
import { fontSize, formatDateTime, instanceStatusMap, metaOf, space } from "@superdl/ui";
import { DataErrorAlert, LoadMore } from "@superdl/ui/components";
import { Empty, Segmented, Space, Tag, Timeline, Tooltip, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useEventReasonText } from "../common";

type EventFilter = "all" | "billing" | "failed";

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
  const [filter, setFilter] = useState<EventFilter>("all");
  // 状态枚举走共享映射表翻译,未知值原样回显
  const statusText = (s: string | null | undefined) => {
    if (!s) return "—";
    const meta = metaOf(instanceStatusMap, s);
    return meta ? t(meta.labelKey) : s;
  };
  const isBoundary = (e: InstanceEventOut) => e.from_status === "running" || e.to_status === "running";
  // 过滤只作用于已加载页(游标分页),LoadMore 照常
  const visible = events.filter((e) =>
    filter === "billing" ? isBoundary(e) : filter === "failed" ? e.to_status === "failed" : true,
  );
  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
      <Space size={space.md} wrap align="center">
        <Segmented<EventFilter>
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: t("instances.eventsAll") },
            { value: "billing", label: t("instances.eventsBillingOnly") },
            { value: "failed", label: t("instances.eventsFailedOnly") },
          ]}
        />
        <Tooltip title={t("copy.eventsAreBilling")}>
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption, cursor: "help" }}>
            <QuestionCircleOutlined /> {t("instances.eventsBillingBasis")}
          </Typography.Text>
        </Tooltip>
      </Space>
      {isError && <DataErrorAlert onRetry={onRetry} />}
      {!isError && !isLoading && visible.length === 0 && (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={filter === "all" ? t("instances.eventsEmpty") : t("instances.eventsNoMatch")}
        />
      )}
      <Timeline
        items={visible.map((e) => ({
          color: e.to_status === "running" ? "green" : e.to_status === "failed" ? "red" : "gray",
          content: (
            <Space orientation="vertical" size={0}>
              <Space size={space.sm} align="center">
                <Typography.Text strong>
                  {statusText(e.from_status)} → {statusText(e.to_status)}
                </Typography.Text>
                {isBoundary(e) && <Tag style={{ marginInlineEnd: 0 }}>{t("instances.billingBoundary")}</Tag>}
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
