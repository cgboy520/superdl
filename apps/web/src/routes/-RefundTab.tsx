/** 退款 Tab:可退订单 → 申请退款 + 我的退款单。 */

import { useTranslation } from "react-i18next";
import { App, Button, Card, Input, InputNumber, Select, Space, Table, Tag, Tooltip, Typography } from "antd";
import { useMemo, useState } from "react";

import { type RefundOut, type RefundableOrderOut } from "@superdl/api-client";
import { fontSize, formatDateTime, idemKeyOf, metaOf, payoutChannelMap, refundStatusMap } from "@superdl/ui";
import { DataErrorAlert, EmptyState, LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { useFormat } from "@superdl/ui";

import { useCreateRefund } from "../api/mutations";
import { useRefundPages, useRefundableOrders } from "../api/queries";

/** 退款:申请表单(仅可申请口径的订单) + 我的退款列表。 */
export const REFUND_REASON_CODE = {
  not_paid: "billing.refundOrderNotPaid",
  already_applied: "billing.refundOrderAlreadyApplied",
  fully_refunded: "billing.refundOrderFullyRefunded",
  invoiced: "billing.refundOrderInvoiced",
  no_balance: "billing.refundOrderNoBalance",
} as const;

export function refundReasonText(
  o: RefundableOrderOut,
  t: (key: (typeof REFUND_REASON_CODE)[keyof typeof REFUND_REASON_CODE]) => string,
): string {
  const key = o.reason_code as keyof typeof REFUND_REASON_CODE | null;
  return key ? t(REFUND_REASON_CODE[key]) : "";
}

export function RefundTab() {
  const { t } = useTranslation(["web", "shared"]);
  const { currencySymbol, formatMoney } = useFormat();
  const { message } = App.useApp();
  const ordersQ = useRefundableOrders();
  const orders = useMemo<RefundableOrderOut[]>(() => ordersQ.data ?? [], [ordersQ.data]);
  const [orderNo, setOrderNo] = useState<string>();
  const [amount, setAmount] = useState("0");
  const [reason, setReason] = useState("");
  // 幂等键按「提交序号 + 表单快照」派生,成功后序号 +1 即新单
  const [submitSeq, setSubmitSeq] = useState(0);
  const selected = orders.find((o) => o.order_no === orderNo);
  const create = useCreateRefund({
    onSuccess: () => {
      message.success(t("billing.refundCreated"));
      setOrderNo(undefined);
      setReason("");
      setSubmitSeq((s) => s + 1);
    },
  });
  const refunds = useRefundPages(20);
  const rows = useMemo<RefundOut[]>(() => (refunds.data?.pages ?? []).flatMap((p) => p.items), [refunds.data]);

  const submit = () => {
    if (!selected || reason.trim().length < 2) return;
    create.mutate({
      body: { order_no: selected.order_no, amount, reason: reason.trim() },
      idempotencyKey: idemKeyOf("refund", [submitSeq, selected.order_no, amount, reason.trim()]),
    });
  };

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Card size="small" title={t("billing.refundApply")}>
        {ordersQ.isError ? (
          // 加载失败不伪装成「无充值订单」
          <DataErrorAlert onRetry={() => void ordersQ.refetch()} />
        ) : orders.length === 0 && !ordersQ.isLoading ? (
          <EmptyState scene="list" compact description={t("billing.refundNoOrders")} />
        ) : (
          <Space orientation="vertical" size={12} style={{ width: "100%" }}>
            <Select
              style={{ width: "100%", maxWidth: 560 }}
              placeholder={t("billing.refundSelectOrder")}
              loading={ordersQ.isLoading}
              value={orderNo}
              onChange={(v: string) => {
                setOrderNo(v);
                // 默认退满上限:min(订单额, 当前余额),与服务端同口径
                setAmount(orders.find((o) => o.order_no === v)?.max_amount ?? "0");
              }}
              options={orders.map((o) => ({
                value: o.order_no,
                disabled: !o.refundable,
                label: o.refundable
                  ? `${o.order_no} · ${formatMoney(o.amount)}`
                  : `${o.order_no} · ${formatMoney(o.amount)}(${refundReasonText(o, t)})`,
              }))}
            />
            <Space wrap align="center">
              <InputNumber
                style={{ width: 180 }}
                min="0.01"
                max={selected?.max_amount ?? "0"}
                precision={2}
                stringMode
                disabled={!selected}
                value={amount}
                onChange={(v) => setAmount(v ?? "0")}
                prefix={currencySymbol}
                aria-label={t("billing.refundAmount")}
              />
              {selected && (
                <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                  {t("billing.refundMaxHint", { amount: formatMoney(selected.max_amount) })}
                </Typography.Text>
              )}
            </Space>
            <Input.TextArea
              rows={2}
              style={{ maxWidth: 560 }}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("billing.refundReasonPlaceholder")}
              maxLength={256}
            />
            <Space align="center" wrap>
              <Tooltip
                title={
                  selected && reason.trim().length < 2 ? t("billing.refundReasonTooShort") : undefined
                }
              >
                <Button
                  type="primary"
                  loading={create.isPending}
                  disabled={!selected || reason.trim().length < 2}
                  onClick={submit}
                >
                  {t("billing.refundSubmit")}
                </Button>
              </Tooltip>
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {t("billing.refundRuleNote")}
              </Typography.Text>
            </Space>
          </Space>
        )}
      </Card>
      <Table
        rowKey="id"
        size="small"
        pagination={false}
        scroll={{ x: 860 }}
        loading={refunds.isLoading}
        dataSource={rows}
        locale={{
          emptyText: refunds.isError ? (
            <TableErrorEmpty isError onRetry={() => void refunds.refetch()} />
          ) : (
            t("billing.refundNone")
          ),
        }}
        columns={[
          { title: t("billing.colTime"), dataIndex: "created_at", render: formatDateTime },
          { title: t("billing.colRefundNo"), dataIndex: "refund_no" },
          { title: t("billing.colOrderNo"), dataIndex: "order_no" },
          {
            title: t("billing.colAmount"),
            render: (_, r) => <span>{formatMoney(r.amount)}</span>,
          },
          {
            title: t("billing.colStatus"),
            render: (_, r) => {
              const m = metaOf(refundStatusMap, r.status);
              return <Tag color={m?.color}>{m ? t(m.labelKey) : r.status}</Tag>;
            },
          },
          {
            title: t("billing.colRejectReason"),
            render: (_, r) => (r.status === "rejected" ? (r.review_comment ?? "—") : "—"),
          },
          {
            title: t("billing.colPayoutChannel"),
            render: (_, r) => {
              if (!r.payout_channel) return "—";
              const m = metaOf(payoutChannelMap, r.payout_channel);
              return m ? t(m.labelKey) : r.payout_channel;
            },
          },
        ]}
      />
      <LoadMore
        hasNextPage={refunds.hasNextPage ?? false}
        loading={refunds.isFetchingNextPage}
        isError={refunds.isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void refunds.fetchNextPage()}
      />
    </Space>
  );
}

