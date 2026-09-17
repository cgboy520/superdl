/** Refunds tab: FilterBar (status / channel / creation day, in the URL) + inline approve / reject / payout registration, cancel folded into more. */

import { useQueryClient } from "@tanstack/react-query";
import { Button, DatePicker, Form, Input, Select, Typography } from "antd";
import dayjs from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  adminColors,
  controlWidth,
  flattenPages,
  fontSize,
  formatDateTime,
  layout,
  metaOf,
  payoutChannelForPayment,
  payoutChannelMap,
  refundStatusMap,
} from "@superdl/ui";
import { CursorTable, EmptyState, FilterBar, GatedButton, Mono, RowActions, RowMoreMenu } from "@superdl/ui/components";
import { useCsvExport, useUrlFilters } from "@superdl/ui";
import { useFormat } from "@superdl/ui";

import {
  type RefundPayout,
  type RefundRow,
  exportRefundsCsv,
  useCancelRefund,
  usePayoutRefund,
  useRefunds,
  useReviewRefund,
} from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { StatusTag } from "@superdl/ui/components";
import { RowActionModal } from "../../components/RowActionModal";
import { tenantColumn } from "../../components/TenantLink";
import { canWriteFinance, useAdminRole, useAuth } from "../../stores/auth";
import { useFinanceFilters } from "./-financeFilters";

/** Payout registration modal: channel + voucher number; money leaves only here. */
export function PayoutModal({
  target,
  onClose,
  onDone,
}: {
  target: RefundRow | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const [form] = Form.useForm<RefundPayout>();
  const payout = usePayoutRefund();
  const [idemFor, setIdemFor] = useState<{ id: number; key: string } | null>(null);
  if (target && idemFor?.id !== target.id) {
    setIdemFor({ id: target.id, key: crypto.randomUUID() });
  }
  if (!target) return null;
  return (
    <RowActionModal
      title={t("finance.payoutTitle", { no: target.refund_no })}
      okText={t("finance.payoutOk")}
      note={t("finance.payoutNote", {
        amount: formatMoney(target.amount),
        reviewer: `#${target.review_by ?? "-"}`,
      })}
      form={form}
      submit={(values) => payout.mutateAsync({ refundId: target.id, data: values, idempotencyKey: idemFor?.key })}
      successText={t("finance.payoutDone")}
      failText={t("finance.payoutFailed")}
      onClose={onClose}
      onDone={onDone}
    >
      <Form.Item name="channel" label={t("finance.payoutChannelLabel")} rules={[{ required: true }]}>
        <Select
          options={Object.entries(payoutChannelMap)
            .filter(([v]) => v === "offline" || v === payoutChannelForPayment[target.order_channel ?? ""])
            .map(([v, m]) => ({
              value: v,
              label: t(m.labelKey),
            }))}
        />
      </Form.Item>
      <Form.Item
        name="ref"
        label={t("finance.payoutRefLabel")}
        rules={[{ required: true, min: 2, message: t("finance.payoutRefRule") }]}
      >
        <Input placeholder={t("finance.payoutRefPlaceholder")} />
      </Form.Item>
    </RowActionModal>
  );
}

export function RefundsTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const role = useAdminRole();
  const { admin } = useAuth();
  const writable = canWriteFinance(role);
  const qc = useQueryClient();
  const { search, setFilters } = useFinanceFilters();
  const status = search.r_status;
  const day = search.r_day ? dayjs(search.r_day) : null;
  const channel = search.r_channel;
  const filters = useUrlFilters({
    search: { r_status: status, r_day: search.r_day, r_channel: channel },
    keys: ["r_status", "r_day", "r_channel"],
    commit: setFilters,
  });
  const params = {
    ...(status ? { status } : {}),
    ...(search.r_day ? { day: search.r_day } : {}),
  };
  const refundsQ = useRefunds(params);
  const { data, queryKey, hasNextPage, isFetchNextPageError } = refundsQ;
  const { doExport, exporting } = useCsvExport((tz, lang) => exportRefundsCsv(params, tz, lang));
  const all = flattenPages(data);
  const rows = channel ? all.filter((r) => r.payout_channel === channel) : all;
  const total = channel ? undefined : (data?.pages[0]?.total ?? undefined);
  const [payoutTarget, setPayoutTarget] = useState<RefundRow | null>(null);
  const review = useReviewRefund();
  const cancel = useCancelRefund();
  const refresh = () => void qc.invalidateQueries({ queryKey });
  const noPerm = t("finance.financeOnlyRefund");

  return (
    <>
      <FilterBar
        hasFilter={filters.hasFilter}
        onClear={filters.clear}
        count={total}
        extra={
          <Button onClick={() => void doExport()} loading={exporting}>
            {t("common.exportCsv")}
          </Button>
        }
      >
        <Select
          allowClear
          placeholder={t("common.statusFilter")}
          style={{ width: controlWidth.sm }}
          value={status}
          onChange={(v) => setFilters({ r_status: v })}
          options={Object.entries(refundStatusMap).map(([v, m]) => ({
            value: v,
            label: t(m.labelKey),
          }))}
        />
        <Select
          allowClear
          placeholder={t("finance.filterPayoutChannel")}
          style={{ width: controlWidth.sm }}
          value={channel}
          onChange={(v) => setFilters({ r_channel: v })}
          options={Object.entries(payoutChannelMap).map(([v, m]) => ({
            value: v,
            label: t(m.labelKey),
          }))}
        />
        <DatePicker
          value={day}
          onChange={(d) => setFilters({ r_day: d ? d.format("YYYY-MM-DD") : undefined })}
          allowClear
        />
      </FilterBar>
      <CursorTable<RefundRow>
        query={refundsQ}
        rows={rows}
        emptyNode={
          <EmptyState
            scene={filters.hasFilter ? "search" : "list"}
            compact
            secondaryAction={
              filters.hasFilter ? (
                <Button size="small" onClick={filters.clear}>
                  {t("filter.clear", { ns: "shared" })}
                </Button>
              ) : undefined
            }
          />
        }
        scroll={{ x: 1100 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        rowKey="id"
        columns={[
          {
            title: t("finance.colRefundNo"),
            dataIndex: "refund_no",
            width: 150,
            fixed: "left",
            render: (v: string) => <Mono>{v}</Mono>,
          },
          tenantColumn(t("finance.colTenant")),
          {
            title: t("finance.colOrderNo"),
            dataIndex: "order_no",
            width: 190,
            render: (v: string) => <Mono>{v}</Mono>,
          },
          {
            title: t("finance.colAmount"),
            dataIndex: "amount",
            width: 100,
            align: "right",
            render: (v: string) => formatMoney(v),
          },
          {
            title: t("finance.colStatus"),
            dataIndex: "status",
            width: 90,
            render: (v: string) => {
              return <StatusTag map={refundStatusMap} value={v} />;
            },
          },
          { title: t("finance.colReason"), dataIndex: "reason", ellipsis: true },
          {
            title: t("finance.colReviewInfo"),
            width: 150,
            render: (_, r) =>
              r.review_by ? (
                <span style={{ color: adminColors.textMuted }}>{`#${r.review_by} ${r.review_comment ?? ""}`}</span>
              ) : (
                "-"
              ),
          },
          {
            title: t("finance.colPayout"),
            width: 190,
            render: (_, r) => {
              if (r.status !== "paid") return "-";
              const m = r.payout_channel ? metaOf(payoutChannelMap, r.payout_channel) : undefined;
              return (
                <span>
                  {m ? t(m.labelKey) : r.payout_channel} · {r.payout_ref}
                  <div style={{ color: adminColors.textMuted, fontSize: fontSize.caption }}>
                    #{r.payout_by} · {r.payout_at ? formatDateTime(r.payout_at) : ""}
                  </div>
                </span>
              );
            },
          },
          { title: t("finance.colCreatedAt"), dataIndex: "created_at", width: 150, render: formatDateTime },
          {
            title: t("finance.colAction"),
            width: 230,
            fixed: "right",
            render: (_, r) => {
              if (r.status !== "pending" && r.status !== "approved") return null;
              const isReviewer = admin?.id === r.review_by;
              const target = `${r.refund_no} · ${formatMoney(r.amount)}`;
              return (
                <RowActions
                  primary={
                    r.status === "pending" ? (
                      <ReasonAction
                        label={t("finance.refundApprove")}
                        target={target}
                        title={t("finance.refundApproveTitle")}
                        confirmText={t("finance.refundApproveConfirm", {
                          amount: formatMoney(r.amount),
                        })}
                        disabled={!writable}
                        disabledReason={noPerm}
                        onSubmit={async (comment) => {
                          await review.mutateAsync({
                            refundId: r.id,
                            data: { approve: true, comment },
                          });
                          refresh();
                        }}
                      />
                    ) : (
                      <GatedButton
                        size="small"
                        type="primary"
                        reason={!writable ? noPerm : isReviewer ? t("finance.refundNoSelfPayout") : undefined}
                        onClick={() => setPayoutTarget(r)}
                      >
                        {t("finance.payout")}
                      </GatedButton>
                    )
                  }
                  secondary={
                    r.status === "pending" ? (
                      <ReasonAction
                        label={t("finance.refundReject")}
                        target={target}
                        title={t("finance.refundRejectTitle")}
                        confirmText={t("finance.refundRejectConfirm", {
                          no: r.refund_no,
                          amount: formatMoney(r.amount),
                        })}
                        danger
                        disabled={!writable}
                        disabledReason={noPerm}
                        onSubmit={async (comment) => {
                          await review.mutateAsync({
                            refundId: r.id,
                            data: { approve: false, comment },
                          });
                          refresh();
                        }}
                      />
                    ) : undefined
                  }
                  more={
                    <RowMoreMenu>
                      <ReasonAction
                        label={t("finance.cancelRefund")}
                        type="text"
                        target={target}
                        title={t("finance.cancelRefundTitle")}
                        confirmText={t("finance.cancelRefundConfirm", {
                          no: r.refund_no,
                          amount: formatMoney(r.amount),
                        })}
                        danger
                        disabled={!writable}
                        disabledReason={noPerm}
                        onSubmit={async (reason) => {
                          await cancel.mutateAsync({ refundId: r.id, data: { reason } });
                          refresh();
                        }}
                      />
                    </RowMoreMenu>
                  }
                />
              );
            },
          },
        ]}
      />
      {channel && !hasNextPage && !isFetchNextPageError && all.length > 0 && (
        <Typography.Text type="secondary" style={{ display: "block", textAlign: "center", padding: "8px 0" }}>
          {t("finance.loadedFilteredNote", { loaded: all.length, shown: rows.length })}
        </Typography.Text>
      )}
      <PayoutModal target={payoutTarget} onClose={() => setPayoutTarget(null)} onDone={refresh} />
    </>
  );
}
