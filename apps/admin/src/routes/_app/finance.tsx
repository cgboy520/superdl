import { adminColors, formatDateTime, metaOf, orderStatusMap, paymentChannelMap } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  App,
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
} from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type AdjustmentRow,
  type AnomalyRow,
  type OrderRow,
  type ReconciliationReport,
  isApiError,
  useAdjustments,
  useAnomalies,
  useBackfillOrder,
  useCreateAdjustment,
  useOrders,
  useReconciliation,
  useReviewAdjustment,
  useVerifyOrder,
} from "../../api";
import { useApiErrorText } from "../../lib/apiError";
import { useFormat } from "../../lib/format";
import { AuditTable } from "../../components/AuditTable";
import { canWriteFinance, useAdminRole, useAuth } from "../../stores/auth";

export const Route = createFileRoute("/_app/finance")({
  component: FinancePage,
});

function ReconciliationCard() {
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const [day, setDay] = useState<Dayjs>(dayjs());
  const { data: report } = useReconciliation(day.format("YYYY-MM-DD"));
  const diffHigh = report != null && report.diff_pct > 2;

  return (
    <Card
      title={t("finance.reconTitle")}
      extra={<DatePicker value={day} onChange={(d) => d && setDay(d)} allowClear={false} />}
    >
      <Row gutter={16}>
        <Col span={6}>
          <Statistic title={t("finance.billedTotal")} value={report ? formatMoney(report.billed_total) : "—"} />
        </Col>
        <Col span={6}>
          <Statistic title={t("finance.estimatedTotal")} value={report ? formatMoney(report.estimated_total) : "—"} />
        </Col>
        <Col span={6}>
          <Statistic
            title="diff%"
            value={report ? report.diff_pct : "—"}
            suffix={report ? "%" : undefined}
            valueStyle={diffHigh ? { color: adminColors.negative } : { color: adminColors.positive }}
          />
        </Col>
      </Row>
      {report && report.outliers.length > 0 && (
        <Table<ReconciliationReport["outliers"][number]>
          size="small"
          style={{ marginTop: 16 }}
          rowKey="instance_id"
          dataSource={report.outliers}
          pagination={false}
          columns={[
            { title: t("finance.colInstanceId"), dataIndex: "instance_id" },
            { title: t("finance.colBilled"), dataIndex: "billed", render: (v: string) => formatMoney(v) },
            { title: t("finance.colEstimated"), dataIndex: "estimated", render: (v: string) => formatMoney(v) },
            {
              title: "diff%",
              dataIndex: "diff_pct",
              render: (v: number) => <Tag color="red">{v}%</Tag>,
            },
          ]}
        />
      )}
    </Card>
  );
}

function OrdersTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const [status, setStatus] = useState<string | undefined>();
  const { data } = useOrders(status ? { status } : undefined);
  const orders: OrderRow[] = data ?? [];
  return (
    <>
      <Select
        allowClear
        placeholder={t("tenants.statusFilter")}
        style={{ width: 160, marginBottom: 12 }}
        value={status}
        onChange={setStatus}
        options={Object.entries(orderStatusMap).map(([v, m]) => ({ value: v, label: t(m.labelKey) }))}
      />
      <Table<OrderRow>
        scroll={{ x: 900 }}
        rowKey="order_no"
        dataSource={orders}
        columns={[
          { title: t("finance.colOrderNo"), dataIndex: "order_no" },
          { title: t("finance.colTenant"), dataIndex: "user_id", width: 80 },
          { title: t("finance.colAmount"), dataIndex: "amount", render: (v: string) => formatMoney(v) },
          {
            title: t("finance.colChannel"),
            dataIndex: "channel",
            render: (v: string) => {
              const m = metaOf(paymentChannelMap, v);
              return m ? t(m.labelKey) : v;
            },
          },
          {
            title: t("finance.colStatus"),
            dataIndex: "status",
            render: (v: string) => {
              const m = metaOf(orderStatusMap, v);
              return <Tag color={m?.color}>{m ? t(m.labelKey) : v}</Tag>;
            },
          },
          { title: t("finance.colCreatedAt"), dataIndex: "created_at", render: formatDateTime },
        ]}
      />
    </>
  );
}

function AdjustmentsTab() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { formatMoney } = useFormat();
  const { message } = App.useApp();
  const role = useAdminRole();
  const { admin } = useAuth();
  const writable = canWriteFinance(role);
  const qc = useQueryClient();
  const { data, queryKey } = useAdjustments();
  const rows: AdjustmentRow[] = data ?? [];
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<{ user_id: number; amount: string; reason: string }>();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  const create = useCreateAdjustment({
    mutation: {
      onSuccess: () => {
        message.success(t("finance.adjustCreated"));
        setCreating(false);
        form.resetFields();
        refresh();
      },
      onError: (e) => message.error(errText(e, t("finance.createFailed"))),
    },
  });
  const review = useReviewAdjustment({
    mutation: {
      onSuccess: () => {
        message.success(t("finance.reviewed"));
        refresh();
      },
      onError: (e) =>
        message.error(
          isApiError(e) && e.code === "ADMIN_SECOND_REVIEW_REQUIRED"
            ? t("finance.noSelfReview")
            : isApiError(e)
              ? e.message
              : t("finance.reviewFailed"),
        ),
    },
  });

  return (
    <>
      <Tooltip title={writable ? "" : t("finance.financeOnlyCreate")}>
        <Button
          type="primary"
          disabled={!writable}
          style={{ marginBottom: 12 }}
          onClick={() => setCreating(true)}
        >
          {t("finance.createAdjust")}
        </Button>
      </Tooltip>
      <Table<AdjustmentRow>
        scroll={{ x: 1000 }}
        rowKey="id"
        dataSource={rows}
        columns={[
          { title: t("finance.colAdjustId"), dataIndex: "id", width: 70 },
          { title: t("finance.colTenant"), dataIndex: "user_id", width: 80 },
          {
            title: t("finance.colAmount"),
            dataIndex: "amount",
            render: (v: string) => (
              <span style={{ color: v.startsWith("-") ? adminColors.negative : adminColors.positive }}>
                {formatMoney(v)}
              </span>
            ),
          },
          { title: t("finance.colReason"), dataIndex: "reason" },
          {
            title: t("finance.colStatus"),
            dataIndex: "status",
            render: (v: string) => (
              <Tag color={{ pending: "blue", approved: "green", rejected: "red" }[v]}>
                {(
                  {
                    pending: t("finance.adjustPending"),
                    approved: t("finance.adjustApproved"),
                    rejected: t("finance.adjustRejected"),
                  } as Record<string, string>
                )[v] ?? v}
              </Tag>
            ),
          },
          { title: t("finance.colCreatedBy"), dataIndex: "created_by", width: 80 },
          {
            title: t("finance.colReview"),
            render: (_, r) => {
              if (r.status !== "pending") {
                return (
                  <span style={{ color: adminColors.textMuted }}>
                    {r.reviewed_by ? `#${r.reviewed_by} ${r.review_comment ?? ""}` : "-"}
                  </span>
                );
              }
              const isCreator = admin?.id === r.created_by;
              return (
                <Tooltip
                  title={
                    !writable
                      ? t("finance.financeOnlyReview")
                      : isCreator
                        ? t("finance.noSelfReviewShort")
                        : ""
                  }
                >
                  <Space>
                    <Popconfirm
                      title={t("finance.approveConfirm", { amount: formatMoney(r.amount) })}
                      onConfirm={() =>
                        review.mutate({ adjustmentId: r.id, data: { approve: true } })
                      }
                      disabled={!writable || isCreator}
                    >
                      <Button size="small" type="primary" disabled={!writable || isCreator}>
                        {t("finance.approve")}
                      </Button>
                    </Popconfirm>
                    <Popconfirm
                      title={t("finance.rejectConfirm")}
                      onConfirm={() =>
                        review.mutate({
                          adjustmentId: r.id,
                          data: { approve: false, comment: t("finance.rejectComment") },
                        })
                      }
                      disabled={!writable || isCreator}
                    >
                      <Button size="small" danger disabled={!writable || isCreator}>
                        {t("finance.reject")}
                      </Button>
                    </Popconfirm>
                  </Space>
                </Tooltip>
              );
            },
          },
          { title: t("finance.colCreatedAtShort"), dataIndex: "created_at", render: formatDateTime },
        ]}
      />
      <Modal
        title={t("finance.createAdjustTitle")}
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={async () => {
          const values = await form.validateFields();
          create.mutate({
            data: {
              user_id: values.user_id,
              amount: values.amount,
              reason: values.reason,
            },
          });
        }}
        okButtonProps={{ loading: create.isPending }}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="user_id" label={t("finance.tenantIdLabel")} rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="amount"
            label={t("finance.amountLabel")}
            rules={[{ required: true }]}
          >
            {/* stringMode:调账金额直接以字符串提交,不经二进制浮点 */}
            <InputNumber
              step="0.01"
              precision={2}
              stringMode
              style={{ width: "100%" }}
              placeholder={t("finance.amountPlaceholder")}
            />
          </Form.Item>
          <Form.Item
            name="reason"
            label={t("common.reasonLabel")}
            rules={[{ required: true, min: 2 }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

const ANOMALY_META = {
  lost_callback: { labelKey: "finance.anomalyLostCallback", color: "orange" },
  closed_order: { labelKey: "finance.anomalyClosedOrder", color: "default" },
  negative_balance: { labelKey: "finance.anomalyNegativeBalance", color: "red" },
} as const;

function AnomaliesTab() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { formatMoney } = useFormat();
  const { message, modal } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteFinance(role);
  const qc = useQueryClient();
  const { data, queryKey, isLoading } = useAnomalies();
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
            <span>{t("finance.verifyChannelAmount", { amount: r.channel_amount ? formatMoney(r.channel_amount) : "—" })}</span>
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
        locale={{ emptyText: t("finance.noAnomalies") }}
        columns={[
          {
            title: t("finance.colKind"),
            dataIndex: "kind",
            width: 110,
            render: (v: AnomalyRow["kind"]) => (
              <Tag color={ANOMALY_META[v].color}>{t(ANOMALY_META[v].labelKey)}</Tag>
            ),
          },
          {
            title: t("finance.colSubject"),
            render: (_, r) => (
              <>
                {r.order_no ?? t("finance.tenantRef", { id: r.user_id })}
                <div style={{ color: adminColors.textSecondary, fontSize: 12 }}>{r.detail}</div>
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
                    <Button
                      size="small"
                      type="primary"
                      disabled={!writable}
                      onClick={() => setBackfillTarget(r)}
                    >
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
          <span style={{ color: adminColors.textSecondary }}>
            {t("finance.backfillNote")}
          </span>
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

function FinancePage() {
  const { t } = useTranslation();
  const { data: anomalies } = useAnomalies();
  const anomalyCount = anomalies?.length ?? 0;
  return (
    <>
      <ReconciliationCard />
      <Card style={{ marginTop: 16 }}>
        <Tabs
          items={[
            { key: "orders", label: t("finance.tabOrders"), children: <OrdersTab /> },
            { key: "adjustments", label: t("finance.tabAdjustments"), children: <AdjustmentsTab /> },
            {
              key: "anomalies",
              label: (
                <Space size={6}>
                  {t("finance.tabAnomalies")}
                  {anomalyCount > 0 && <Tag color="red">{anomalyCount}</Tag>}
                </Space>
              ),
              children: <AnomaliesTab />,
            },
            { key: "audit", label: t("menu.audit"), children: <AuditTable /> },
          ]}
        />
      </Card>
    </>
  );
}
