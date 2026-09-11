/** 退款 Tab:审批 / 驳回 / 打款登记 / 取消(更多)。 */

import { useQueryClient } from "@tanstack/react-query";
import { Button, DatePicker, Form, Input, Select, Space, Table, Tooltip, Typography } from "antd";
import dayjs from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { adminColors, fontSize, formatDateTime, layout, metaOf, payoutChannelMap, refundStatusMap } from "@superdl/ui";
import { HexTag, LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { useCsvExport } from "@superdl/ui";
import { useFormat } from "@superdl/ui";

import { type RefundPayout, type RefundRow, exportRefundsCsv, isApiError, useCancelRefund, usePayoutRefund, useRefunds, useReviewRefund } from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { RowMoreMenu } from "../../components/RowMoreMenu";
import { RowActionModal } from "../../components/RowActionModal";
import { tenantColumn } from "../../components/TenantLink";
import { canWriteFinance, useAdminRole, useAuth } from "../../stores/auth";
import { useFinanceFilters } from "./-financeFilters";

/** 登记打款弹窗:渠道 + 凭证号;出金只发生在这里。 */
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
  // 幂等键随目标单派生
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
      submit={(values) =>
        payout.mutateAsync({ refundId: target.id, data: values, idempotencyKey: idemFor?.key })
      }
      successText={t("finance.payoutDone")}
      failText={t("finance.payoutFailed")}
      onClose={onClose}
      onDone={onDone}
    >
      <Form.Item name="channel" label={t("finance.payoutChannelLabel")} rules={[{ required: true }]}>
        <Select
          options={Object.entries(payoutChannelMap).map(([v, m]) => ({
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
  // 筛选条件入 URL(status/发起日/渠道);渠道在客户端过滤已加载页
  const { search, setFilters } = useFinanceFilters();
  const status = search.r_status;
  const day = search.r_day ? dayjs(search.r_day) : null;
  const channel = search.r_channel;
  // 渠道不进导出参数
  const params = {
    ...(status ? { status } : {}),
    ...(search.r_day ? { day: search.r_day } : {}),
  };
  const { data, queryKey, isLoading, isError, error, refetch, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = useRefunds(params);
  const { doExport, exporting } = useCsvExport((tz, lang) => exportRefundsCsv(params, tz, lang));
  const all: RefundRow[] = data?.pages.flatMap((p) => p.items) ?? [];
  const rows = channel ? all.filter((r) => r.payout_channel === channel) : all;
  const [payoutTarget, setPayoutTarget] = useState<RefundRow | null>(null);
  const review = useReviewRefund();
  const cancel = useCancelRefund();
  const refresh = () => void qc.invalidateQueries({ queryKey });
  const noPerm = t("finance.financeOnlyRefund");

  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder={t("common.statusFilter")}
          style={{ width: 150 }}
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
          style={{ width: 150 }}
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
        <Button onClick={() => void doExport()} loading={exporting}>
          {t("common.exportCsv")}
        </Button>
      </Space>
      <Table<RefundRow>
        scroll={{ x: 1100 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        rowKey="id"
        loading={isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={isError}
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            />
          ),
        }}
        dataSource={rows}
        columns={[
          {
            title: t("finance.colRefundNo"),
            dataIndex: "refund_no",
            width: 150,
            fixed: "left",
            render: (v: string) => <span className="mono">{v}</span>,
          },
          tenantColumn(t("finance.colTenant")),
          { title: t("finance.colOrderNo"), dataIndex: "order_no", width: 190 },
          {
            title: t("finance.colAmount"),
            dataIndex: "amount",
            width: 100,
            render: (v: string) => formatMoney(v),
          },
          {
            title: t("finance.colStatus"),
            dataIndex: "status",
            width: 90,
            render: (v: string) => {
              const m = metaOf(refundStatusMap, v);
              return <HexTag color={m?.color}>{m ? t(m.labelKey) : v}</HexTag>;
            },
          },
          { title: t("finance.colReason"), dataIndex: "reason", ellipsis: true },
          {
            title: t("finance.colReviewInfo"),
            width: 150,
            render: (_, r) =>
              r.review_by ? (
                <span style={{ color: adminColors.textMuted }}>
                  {`#${r.review_by} ${r.review_comment ?? ""}`}
                </span>
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
              return (
                <Space wrap>
                  {r.status === "pending" && (
                    <>
                      <ReasonAction
                        label={t("finance.refundApprove")}
                        target={`${r.refund_no} · ${formatMoney(r.amount)}`}
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
                      <ReasonAction
                        label={t("finance.refundReject")}
                        target={`${r.refund_no} · ${formatMoney(r.amount)}`}
                        title={t("finance.refundRejectTitle")}
                        confirmText={t("finance.refundRejectConfirm", { no: r.refund_no, amount: formatMoney(r.amount) })}
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
                    </>
                  )}
                  {r.status === "approved" && (
                    <Tooltip
                      title={
                        !writable
                          ? noPerm
                          : isReviewer
                            ? t("finance.refundNoSelfPayout")
                            : ""
                      }
                    >
                      <Button
                        size="small"
                        type="primary"
                        disabled={!writable || isReviewer}
                        onClick={() => setPayoutTarget(r)}
                      >
                        {t("finance.payout")}
                      </Button>
                    </Tooltip>
                  )}
                  {/* 低频的「取消退款单」收进更多(行内 ≤2 动作) */}
                  <RowMoreMenu>
                    <ReasonAction
                      label={t("finance.cancelRefund")}
                      type="text"
                      target={`${r.refund_no} · ${formatMoney(r.amount)}`}
                      title={t("finance.cancelRefundTitle")}
                      confirmText={t("finance.cancelRefundConfirm", { no: r.refund_no, amount: formatMoney(r.amount) })}
                      danger
                      disabled={!writable}
                      disabledReason={noPerm}
                      onSubmit={async (reason) => {
                        await cancel.mutateAsync({ refundId: r.id, data: { reason } });
                        refresh();
                      }}
                    />
                  </RowMoreMenu>
                </Space>
              );
            },
          },
        ]}
      />
      <LoadMore
        hasNextPage={Boolean(hasNextPage)}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        // 渠道过滤时隐藏计数,改由下方汇总行展示
        loadedCount={channel ? undefined : all.length}
        onLoadMore={() => void fetchNextPage()}
      />
      {channel && !hasNextPage && !isFetchNextPageError && all.length > 0 && (
        <Typography.Text
          type="secondary"
          style={{ display: "block", textAlign: "center", padding: "8px 0" }}
        >
          {t("finance.loadedFilteredNote", { loaded: all.length, shown: rows.length })}
        </Typography.Text>
      )}
      <PayoutModal
        target={payoutTarget}
        onClose={() => setPayoutTarget(null)}
        onDone={refresh}
      />
    </>
  );
}

