/** 收支明细 Tab:类型筛选 + 游标加载更多。 */

import { useNavigate, getRouteApi } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Select, Space, Table, Tag, theme } from "antd";
import { useMemo } from "react";

import { type LedgerEntryOut } from "@superdl/api-client";
import { formatDateTime, ledgerTypeMap, metaOf } from "@superdl/ui";
import { EmptyState, LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { useFormat } from "@superdl/ui";

import { useLedgerPages } from "../api/queries";
import { BillingTab } from "./_console.billing";

const routeApi = getRouteApi("/_console/billing");

/** 收支明细类型筛选(URL ?ledger=);值域与后端 LedgerType 一致 */
export const LEDGER_FILTERS = ["recharge", "consume", "refund", "adjust"] as const;
export type LedgerFilter = (typeof LEDGER_FILTERS)[number];

/** 资金流水:游标分页 + 「加载更多」;金额一律按字符串渲染。 */
export function LedgerTable() {
  const { t } = useTranslation(["web", "shared"]);
  const { formatMoney } = useFormat();
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const { ledger: ledgerFilter } = routeApi.useSearch();
  const {
    data,
    isLoading,
    isError,
    refetch,
    isFetchingNextPage,
    isFetchNextPageError,
    hasNextPage,
    fetchNextPage,
  } = useLedgerPages(20);
  const merged = useMemo<LedgerEntryOut[]>(
    () => (data?.pages ?? []).flatMap((p) => p.items),
    [data],
  );
  // 类型筛选为客户端筛选,只作用于已加载页
  const filtered = useMemo<LedgerEntryOut[]>(
    () => (ledgerFilter ? merged.filter((r) => r.type === ledgerFilter) : merged),
    [merged, ledgerFilter],
  );

  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <Select
        style={{ width: 160 }}
        aria-label={t("billing.colType")}
        value={ledgerFilter ?? "all"}
        onChange={(v: string) =>
          void navigate({
            to: "/billing",
            search: (prev) => ({
              // prev 必带本页 search;tab 在此文件内联合类型收窄
              tab: prev.tab as BillingTab | undefined,
              month: prev.month,
              ledger: v === "all" ? undefined : (v as LedgerFilter),
            }),
            replace: true,
          })
        }
        options={[
          { value: "all", label: t("billing.ledgerFilterAll") },
          ...LEDGER_FILTERS.map((f) => {
            const meta = metaOf(ledgerTypeMap, f);
            // 裸类型码不进 t()(extract 会当成新键)
            return { value: f, label: meta ? t(meta.labelKey) : f };
          }),
        ]}
      />
      <Table
        rowKey="id"
        size="small"
        pagination={false}
        scroll={{ x: 760 }}
        loading={isLoading}
        dataSource={filtered}
        locale={{
          emptyText: isError ? (
            <TableErrorEmpty isError onRetry={() => void refetch()} />
          ) : (
            <EmptyState scene="list" compact description={t("billing.ledgerEmpty")} />
          ),
        }}
        columns={[
          { title: t("billing.colTime"), render: (_, r) => formatDateTime(r.created_at) },
          {
            title: t("billing.colType"),
            render: (_, r) => {
              const meta = metaOf(ledgerTypeMap, r.type);
              return <Tag color={meta?.color ?? "default"}>{meta ? t(meta.labelKey) : r.type}</Tag>;
            },
          },
          {
            title: t("billing.colAmount"),
            render: (_, r) => (
              // 收入绿/支出红
              <span
                style={{ color: r.amount.startsWith("-") ? token.colorError : token.colorSuccess }}
              >
                {r.amount.startsWith("-") ? "" : "+"}
                {formatMoney(r.amount)}
              </span>
            ),
          },
          {
            title: t("billing.colBalanceAfter"),
            render: (_, r) => <span>{formatMoney(r.balance_after)}</span>,
          },
          { title: t("billing.colRemark"), dataIndex: "remark" },
        ]}
      />
      <LoadMore
        hasNextPage={hasNextPage ?? false}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={filtered.length}
        onLoadMore={() => void fetchNextPage()}
      />
    </Space>
  );
}

