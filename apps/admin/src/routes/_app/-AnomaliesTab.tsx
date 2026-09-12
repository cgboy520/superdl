/** 支付异常 Tab:渠道回调与订单状态不一致的清单与处置。 */

import { useQueryClient } from "@tanstack/react-query";
import { App, Button, Form, Input, Modal, Space, Table, Tag, Tooltip } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { adminColors, fontSize, formatDateTime, idemKeyOf } from "@superdl/ui";
import { TableErrorEmpty } from "@superdl/ui/components";
import { useApiErrorText } from "@superdl/ui";
import { useFormat } from "@superdl/ui";

import { type AnomalyRow, isApiError, useAnomalies, useBackfillOrder, useVerifyOrder } from "../../api";
import { TenantLink } from "../../components/TenantLink";
import { canWriteFinance, useAdminRole } from "../../stores/auth";

export const ANOMALY_META = {
  lost_callback: { labelKey: "finance.anomalyLostCallback", color: "orange" },
  closed_order: { labelKey: "finance.anomalyClosedOrder", color: "default" },
  failed_order: { labelKey: "finance.anomalyFailedOrder", color: "volcano" },
  channel_reversed: { labelKey: "finance.anomalyChannelReversed", color: "magenta" },
  negative_balance: { labelKey: "finance.anomalyNegativeBalance", color: "red" },
} as const;

export function AnomaliesTab() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { formatMoney } = useFormat();
  const { message, modal } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteFinance(role);
  const qc = useQueryClient();
  const { data, queryKey, isLoading, isError, error, refetch } = useAnomalies();
  const rows: AnomalyRow[] = data ?? [];
  const verify = useVerifyOrder();
  const backfill = useBackfillOrder();
  const [backfillTarget, setBackfillTarget] = useState<AnomalyRow | null>(null);
  const [reasonForm] = Form.useForm<{ reason: string }>();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  const doVerify = async (orderNo: string) => {
    try {
      const r = await verify.mutateAsync({ orderNo });
      modal.info({
        title: t("finance.verifyTitle", { no: orderNo }),
        content: (
          <Space orientation="vertical" size={4}>
            <span>{t("finance.verifyOrderLine", { status: r.order_status, amount: formatMoney(r.order_amount) })}</span>
            <span>{t("finance.verifyChannelStatus", { status: r.channel_status })}</span>
            <span>
              {t("finance.verifyChannelAmount", { amount: r.channel_amount ? formatMoney(r.channel_amount) : "—" })}
            </span>
            <span>{t("finance.verifyChannelTxn", { id: r.channel_txn_id ?? "—" })}</span>
            <b style={{ color: r.matches ? adminColors.positive : adminColors.negative }}>
              {r.matches ? t("finance.verifyMatch") : t("finance.verifyMismatch")}
            </b>
          </Space>
        ),
      });
    } catch (e) {
      message.error(errText(e, t("finance.verifyFailed")));
    }
  };

  return (
    <>
      <Table<AnomalyRow>
        scroll={{ x: 960 }}
        rowKey={(r) => `${r.kind}:${r.order_no ?? r.user_id}`}
        loading={isLoading}
        dataSource={rows}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={isError}
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            >
              {t("finance.noAnomalies")}
            </TableErrorEmpty>
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
                {r.order_no ?? <TenantLink id={r.user_id} />}
                <div style={{ color: adminColors.textSecondary, fontSize: fontSize.caption }}>{r.detail}</div>
              </>
            ),
          },
          { title: t("finance.colAmount"), dataIndex: "amount", width: 110, render: (v: string) => formatMoney(v) },
          { title: t("finance.colFoundAt"), dataIndex: "created_at", width: 150, render: formatDateTime },
          {
            title: t("finance.colAction"),
            width: 200,
            render: (_, r) => {
              if (r.kind === "negative_balance") {
                return <span style={{ color: adminColors.textSecondary }}>{t("finance.negativeBalanceHint")}</span>;
              }
              return (
                <Space>
                  <Button size="small" onClick={() => void doVerify(r.order_no!)}>
                    {t("finance.verifyChannel")}
                  </Button>
                  <Tooltip title={writable ? "" : t("finance.financeOnlyBackfill")}>
                    <Button size="small" type="primary" disabled={!writable} onClick={() => setBackfillTarget(r)}>
                      {t("finance.backfill")}
                    </Button>
                  </Tooltip>
                </Space>
              );
            },
          },
        ]}
      />
      <Modal
        title={t("finance.backfillTitle", { no: backfillTarget?.order_no ?? "" })}
        open={Boolean(backfillTarget)}
        onCancel={() => setBackfillTarget(null)}
        okText={t("finance.backfillOk")}
        okButtonProps={{ loading: backfill.isPending }}
        onOk={async () => {
          const { reason } = await reasonForm.validateFields();
          try {
            await backfill.mutateAsync({
              orderNo: backfillTarget!.order_no!,
              data: { reason },
              // 幂等键从快照派生
              idempotencyKey: idemKeyOf("backfill", [backfillTarget!.order_no!, reason]),
            });
            message.success(t("finance.backfilled"));
            setBackfillTarget(null);
            reasonForm.resetFields();
            refresh();
          } catch (e) {
            message.error(errText(e, t("finance.backfillFailed")));
          }
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <span style={{ color: adminColors.textSecondary }}>{t("finance.backfillNote")}</span>
          <Form form={reasonForm} layout="vertical">
            <Form.Item
              name="reason"
              label={t("common.reasonLabel")}
              rules={[{ required: true, min: 2, message: t("common.reasonRule") }]}
            >
              <Input.TextArea rows={2} placeholder={t("finance.backfillReasonPlaceholder")} />
            </Form.Item>
          </Form>
        </Space>
      </Modal>
    </>
  );
}
