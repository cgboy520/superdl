/** 小时账单表:费用中心(按月,带实例列)与实例详情账单 Tab(按实例)同一张表。
 *  两处的列口径必须一致 —— 各写一份就会出现「详情页时长按秒、费用中心按分」这类对不上的账。
 *  金额一律按字符串渲染,不过 Number。 */

import type { BillHourlyOut, ListHourlyBillsApiV1BillsHourlyGetParams } from "@superdl/api-client";
import { formatDateTime } from "@superdl/ui";
import { EmptyState, LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { Space, Table } from "antd";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { useHourlyBillPages } from "../api/queries";
import { useFormat } from "@superdl/ui";

export function HourlyBillsTable({
  params,
  showInstance = false,
}: {
  params: Omit<ListHourlyBillsApiV1BillsHourlyGetParams, "cursor" | "limit">;
  /** 费用中心跨实例查,需要实例列;实例详情页本来就在一台机器的上下文里,不重复显示 */
  showInstance?: boolean;
}) {
  const { t } = useTranslation();
  const { formatDuration, formatHourlyPrice, formatMoney } = useFormat();
  const {
    data,
    isLoading,
    isError,
    refetch,
    isFetchingNextPage,
    isFetchNextPageError,
    hasNextPage,
    fetchNextPage,
  } = useHourlyBillPages(params);
  const rows = useMemo<BillHourlyOut[]>(() => (data?.pages ?? []).flatMap((p) => p.items), [data]);

  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <Table
        rowKey="id"
        size="small"
        pagination={false}
        // 多一列实例名后窄屏放不下,给横向滚动
        scroll={showInstance ? { x: 760 } : undefined}
        loading={isLoading}
        dataSource={rows}
        locale={{
          emptyText: isError ? (
            <TableErrorEmpty isError onRetry={() => void refetch()} />
          ) : (
            <EmptyState scene="list" compact description={t("billing.hourlyEmpty")} />
          ),
        }}
        columns={[
          { title: t("instances.colBillHour"), render: (_, r) => formatDateTime(r.hour_start) },
          ...(showInstance
            ? [
                {
                  title: t("billing.colInstance"),
                  render: (_: unknown, r: BillHourlyOut) =>
                    r.instance_name ?? `#${r.instance_id}`,
                },
              ]
            : []),
          { title: t("instances.colBillDuration"), render: (_, r) => formatDuration(r.seconds_used) },
          {
            title: t("instances.colBillUnit"),
            render: (_, r) => (
              <span>
                {formatHourlyPrice(r.unit_price)} × {r.gpu_count}
              </span>
            ),
          },
          {
            title: t("instances.colBillAmount"),
            render: (_, r) => <span>{formatMoney(r.amount)}</span>,
          },
        ]}
      />
      <LoadMore
        hasNextPage={hasNextPage ?? false}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void fetchNextPage()}
      />
    </Space>
  );
}
