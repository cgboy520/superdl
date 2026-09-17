/** Payment anomalies tab: list and disposal of orders whose channel callback disagrees with the order status (backfill primary / verify secondary; the dialog and backfill modal live in components/orderActions). */

import { useQueryClient } from "@tanstack/react-query";
import { Button, Table, Tag } from "antd";
import { useTranslation } from "react-i18next";

import { adminColors, fontSize, formatDateTime } from "@superdl/ui";
import { EmptyState, GatedButton, Mono, RowActions, TableErrorEmpty } from "@superdl/ui/components";
import { useFormat } from "@superdl/ui";

import { type AnomalyRow, isApiError, useAnomalies } from "../../api";
import { useOrderActions } from "../../components/orderActions";
import { TenantLink } from "../../components/TenantLink";

export const ANOMALY_META = {
  lost_callback: { labelKey: "finance.anomalyLostCallback", color: "orange" },
  closed_order: { labelKey: "finance.anomalyClosedOrder", color: "default" },
  failed_order: { labelKey: "finance.anomalyFailedOrder", color: "volcano" },
  channel_reversed: { labelKey: "finance.anomalyChannelReversed", color: "magenta" },
  negative_balance: { labelKey: "finance.anomalyNegativeBalance", color: "red" },
} as const;

export function AnomaliesTab() {
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const qc = useQueryClient();
  const { data, queryKey, isLoading, isError, error, refetch } = useAnomalies();
  const rows: AnomalyRow[] = data ?? [];
  const actions = useOrderActions(() => void qc.invalidateQueries({ queryKey }));
  const writable = actions.writable;

  return (
    <>
      <Table<AnomalyRow>
        scroll={{ x: 960 }}
        rowKey={(r) => `${r.kind}:${r.order_no ?? r.user_id}`}
        loading={isLoading}
        dataSource={rows}
        locale={{
          emptyText: isError ? (
            <TableErrorEmpty
              isError
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            />
          ) : (
            <EmptyState scene="list" compact description={t("finance.noAnomalies")} />
          ),
        }}
        columns={[
          {
            title: t("finance.colKind"),
            dataIndex: "kind",
            width: 110,
            render: (v: AnomalyRow["kind"]) => <Tag color={ANOMALY_META[v].color}>{t(ANOMALY_META[v].labelKey)}</Tag>,
          },
          {
            title: t("finance.colSubject"),
            render: (_, r) => (
              <>
                {r.order_no ? <Mono>{r.order_no}</Mono> : <TenantLink id={r.user_id} />}
                <div style={{ color: adminColors.textSecondary, fontSize: fontSize.caption }}>{r.detail}</div>
              </>
            ),
          },
          {
            title: t("finance.colAmount"),
            dataIndex: "amount",
            width: 110,
            align: "right",
            render: (v: string) => formatMoney(v),
          },
          { title: t("finance.colFoundAt"), dataIndex: "created_at", width: 150, render: formatDateTime },
          {
            title: t("finance.colAction"),
            width: 200,
            render: (_, r) => {
              if (r.kind === "negative_balance") {
                return <span style={{ color: adminColors.textSecondary }}>{t("finance.negativeBalanceHint")}</span>;
              }
              const orderNo = r.order_no;
              if (!orderNo) return null;
              return (
                <RowActions
                  primary={
                    <GatedButton
                      size="small"
                      type="primary"
                      reason={writable ? undefined : t("finance.financeOnlyBackfill")}
                      onClick={() => actions.openBackfill(orderNo)}
                    >
                      {t("finance.backfill")}
                    </GatedButton>
                  }
                  secondary={
                    <Button size="small" onClick={() => actions.verify(orderNo)}>
                      {t("finance.verifyChannel")}
                    </Button>
                  }
                />
              );
            },
          },
        ]}
      />
      {actions.modals}
    </>
  );
}
