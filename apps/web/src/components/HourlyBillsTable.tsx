/** Hourly bill table: billing page (per month, with the instance column), instance detail Bills tab and service detail Bills tab share one table with the same columns. Query results are injected by the caller; amounts are always rendered as strings. */

import type { BillHourlyOut, PageBillHourlyOut } from "@superdl/api-client";
import { flattenPages, formatDateTime } from "@superdl/ui";
import { EmptyState, LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import type { InfiniteData, UseInfiniteQueryResult } from "@tanstack/react-query";
import { Space, Table } from "antd";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";

export type HourlyBillsQuery = UseInfiniteQueryResult<InfiniteData<PageBillHourlyOut>>;

export function HourlyBillsTable({
  query,
  showInstance = false,
}: {
  query: HourlyBillsQuery;
  /** The billing page's cross-instance query needs the instance column; instance / service details hide it */
  showInstance?: boolean;
}) {
  const { t } = useTranslation();
  const { formatDuration, formatHourlyPrice, formatMoney } = useFormat();
  const { data, isLoading, isError, refetch, isFetchingNextPage, isFetchNextPageError, hasNextPage, fetchNextPage } =
    query;
  const rows = useMemo(() => flattenPages(data), [data]);

  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <Table
        rowKey="id"
        size="small"
        pagination={false}
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
                  render: (_: unknown, r: BillHourlyOut) => r.instance_name ?? `#${r.instance_id}`,
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
        hasNextPage={hasNextPage}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void fetchNextPage()}
      />
    </Space>
  );
}
