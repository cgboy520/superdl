/** Ledger tab: type filter + cursor load more. */

import { useNavigate, getRouteApi } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Select, Space, Table, Tag, theme } from "antd";
import { useMemo } from "react";

import { type LedgerEntryOut } from "@superdl/api-client";
import { flattenPages, formatDateTime, ledgerTypeMap, metaOf } from "@superdl/ui";
import { EmptyState, LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { useFormat } from "@superdl/ui";

import { useLedgerPages } from "../api/queries";
import { BillingTab } from "./_console.billing";

const routeApi = getRouteApi("/_console/billing");

/** Ledger type filter (URL ?ledger=); values match the backend LedgerType */
export const LEDGER_FILTERS = ["recharge", "consume", "refund", "adjust"] as const;
export type LedgerFilter = (typeof LEDGER_FILTERS)[number];

/** Ledger: cursor pagination + "load more"; amounts are always rendered as strings. */
export function LedgerTable() {
  const { t } = useTranslation(["web", "shared"]);
  const { formatMoney } = useFormat();
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const { ledger: ledgerFilter } = routeApi.useSearch();
  const { data, isLoading, isError, refetch, isFetchingNextPage, isFetchNextPageError, hasNextPage, fetchNextPage } =
    useLedgerPages(20);
  const merged = useMemo(() => flattenPages(data), [data]);
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
              <span style={{ color: r.amount.startsWith("-") ? token.colorError : token.colorSuccess }}>
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
        hasNextPage={hasNextPage}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={filtered.length}
        onLoadMore={() => void fetchNextPage()}
      />
    </Space>
  );
}
