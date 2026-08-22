import { adminColors, formatDateTime, ledgerTypeMap, metaOf, orderStatusMap, paymentChannelMap } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type AdjustmentRow,
  type AnomalyRow,
  type OrderRow,
  type ReconciliationReport,
  useAdjustContext,
  useAdjustments,
  useAnomalies,
  useBackfillOrder,
  useCreateAdjustment,
  useOrders,
  useReconciliation,
  useReviewAdjustment,
  useVerifyOrder,
} from "../../api";
import { LIST_CAPS, ListCapNote } from "../../components/ListCapNote";
import { useApiErrorText } from "../../lib/apiError";
import { useFormat } from "../../lib/format";
import { AuditTable } from "../../components/AuditTable";
import { StatusTag } from "../../components/StatusTag";
import { TenantLink } from "../../components/TenantLink";
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
        <Col xs={24} sm={12} md={8}>
          <Statistic title={t("finance.billedTotal")} value={report ? formatMoney(report.billed_total) : "—"} />
        </Col>
        <Col xs={24} sm={12} md={8}>
          <Statistic title={t("finance.estimatedTotal")} value={report ? formatMoney(report.estimated_total) : "—"} />
        </Col>
        <Col xs={24} sm={12} md={8}>
          <Statistic
            title="diff%"
            value={report ? report.diff_pct : "—"}
            suffix={report ? "%" : undefined}
            styles={{
              content: diffHigh
                ? { color: adminColors.negative }
                : { color: adminColors.positive },
            }}
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
  const [orderNo, setOrderNo] = useState("");
  const { data } = useOrders({
    ...(status ? { status } : {}),
    ...(orderNo ? { order_no: orderNo } : {}),
  });
  const orders: OrderRow[] = data ?? [];
  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
      <Select
        allowClear
        placeholder={t("tenants.statusFilter")}
        style={{ width: 160 }}
        value={status}
        onChange={setStatus}
        options={Object.entries(orderStatusMap).map(([v, m]) => ({ value: v, label: t(m.labelKey) }))}
      />
      <Input.Search
        allowClear
        placeholder={t("finance.searchOrderPlaceholder")}
        style={{ width: 260 }}
        onSearch={setOrderNo}
      />
      </Space>
      <Table<OrderRow>
        scroll={{ x: 900 }}
        rowKey="order_no"
        dataSource={orders}
        columns={[
          { title: t("finance.colOrderNo"), dataIndex: "order_no" },
          {
            title: t("finance.colTenant"),
            dataIndex: "user_id",
            width: 80,
            render: (v: number) => <TenantLink id={v} />,
          },
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
              return <StatusTag color={m?.color}>{m ? t(m.labelKey) : v}</StatusTag>;
            },
          },
          { title: t("finance.colCreatedAt"), dataIndex: "created_at", render: formatDateTime },
        ]}
      />
      <ListCapNote rows={orders.length} cap={LIST_CAPS.orders} />
    </>
  );
}

// 与 adminapi/service.ADJUST_MAX_ABS 对齐:单笔绝对值上限,超出走对公/线下流程
const ADJUST_MAX_ABS = 100000;

/** 复核确认框:列出租户/当前余额/调账后余额/发起人/原因(不再是只有金额的一句话)。 */
function ReviewConfirmModal({
  target,
  onClose,
  onReviewed,
}: {
  target: { adj: AdjustmentRow; approve: boolean } | null;
  onClose: () => void;
  onReviewed: () => void;
}) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { formatMoney } = useFormat();
  const { message } = App.useApp();
  const ctx = useAdjustContext(target?.adj.user_id ?? null);
  const review = useReviewAdjustment({
    mutation: {
      onSuccess: () => {
        message.success(t("finance.reviewed"));
        onReviewed();
        onClose();
      },
      // 统一走 message_key 目录映射,不直接展示 e.message(#254)
      onError: (e) => message.error(errText(e, t("finance.reviewFailed"))),
    },
  });
  if (!target) return null;
  const { adj, approve } = target;
  const balance = ctx.data ? Number(ctx.data.balance) : null;
  const afterCents =
    balance === null ? null : Math.round(balance * 100) + Math.round(Number(adj.amount) * 100);
  return (
    <Modal
      open
      title={approve ? t("finance.approveTitle") : t("finance.rejectTitle")}
      okText={approve ? t("finance.approve") : t("finance.reject")}
      okButtonProps={{ danger: !approve, loading: review.isPending, disabled: ctx.isError }}
      onCancel={onClose}
      onOk={() =>
        review.mutate({
          adjustmentId: adj.id,
          data: approve
            ? { approve: true }
            : { approve: false, comment: t("finance.rejectComment") },
        })
      }
    >
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label={t("finance.colTenant")}>
          #{adj.user_id}
          {ctx.data ? ` · ${ctx.data.phone_masked}` : ""}
          {ctx.data?.status === "frozen" ? ` · ${t("tenants.frozen")}` : ""}
        </Descriptions.Item>
        <Descriptions.Item label={t("finance.colAmount")}>
          <span style={{ color: adj.amount.startsWith("-") ? adminColors.negative : adminColors.positive }}>
            {formatMoney(adj.amount)}
          </span>
        </Descriptions.Item>
        <Descriptions.Item label={t("finance.ctxBalance")}>
          {ctx.isLoading ? "…" : ctx.data ? formatMoney(ctx.data.balance) : "—"}
        </Descriptions.Item>
        {approve && (
          <Descriptions.Item label={t("finance.ctxBalanceAfter")}>
            {afterCents === null ? "—" : formatMoney((afterCents / 100).toFixed(2))}
          </Descriptions.Item>
        )}
        <Descriptions.Item label={t("finance.colCreatedBy")}>#{adj.created_by}</Descriptions.Item>
        <Descriptions.Item label={t("finance.colReason")}>{adj.reason}</Descriptions.Item>
      </Descriptions>
      {ctx.isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 12 }}
          title={t("finance.tenantNotFound")}
        />
      )}
    </Modal>
  );
}

function AdjustmentsTab() {
  const { t } = useTranslation(["admin", "shared"]);
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
  const [reviewTarget, setReviewTarget] = useState<{ adj: AdjustmentRow; approve: boolean } | null>(null);
  const [form] = Form.useForm<{ user_id: number; amount: string; reason: string }>();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  // 输入 user_id 即时回显租户身份与资金现状;不存在则阻止提交
  const wUserId = Form.useWatch("user_id", form);
  const ctxId = typeof wUserId === "number" && Number.isInteger(wUserId) && wUserId > 0 ? wUserId : null;
  const ctx = useAdjustContext(creating ? ctxId : null);

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
          {
            title: t("finance.colTenant"),
            dataIndex: "user_id",
            width: 80,
            render: (v: number) => <TenantLink id={v} />,
          },
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
                    <Button
                      size="small"
                      type="primary"
                      disabled={!writable || isCreator}
                      onClick={() => setReviewTarget({ adj: r, approve: true })}
                    >
                      {t("finance.approve")}
                    </Button>
                    <Button
                      size="small"
                      danger
                      disabled={!writable || isCreator}
                      onClick={() => setReviewTarget({ adj: r, approve: false })}
                    >
                      {t("finance.reject")}
                    </Button>
                  </Space>
                </Tooltip>
              );
            },
          },
          { title: t("finance.colCreatedAtShort"), dataIndex: "created_at", render: formatDateTime },
        ]}
      />
      <ListCapNote rows={rows.length} cap={LIST_CAPS.adjustments} />
      <ReviewConfirmModal
        target={reviewTarget}
        onClose={() => setReviewTarget(null)}
        onReviewed={refresh}
      />
      <Modal
        title={t("finance.createAdjustTitle")}
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={async () => {
          const values = await form.validateFields();
          // 上下文必须已确认(不存在/查询失败都阻止提交;服务端再拦一道)
          if (!ctx.data) return;
          create.mutate({
            data: {
              user_id: values.user_id,
              amount: values.amount,
              reason: values.reason,
            },
          });
        }}
        okButtonProps={{ loading: create.isPending, disabled: ctxId === null || !ctx.data }}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="user_id" label={t("finance.tenantIdLabel")} rules={[{ required: true }]}>
            <InputNumber min={1} precision={0} style={{ width: "100%" }} />
          </Form.Item>
          {ctxId !== null && (
            <div style={{ marginTop: -8, marginBottom: 16 }}>
              {ctx.isLoading && <Spin size="small" />}
              {ctx.isError && (
                <Typography.Text type="danger">{t("finance.tenantNotFound")}</Typography.Text>
              )}
              {ctx.data && (
                <Alert
                  type={ctx.data.status === "frozen" ? "warning" : "info"}
                  showIcon
                  title={
                    <Space size={12} wrap>
                      <span>{ctx.data.phone_masked}</span>
                      <span>
                        {ctx.data.status === "frozen" ? t("tenants.frozen") : t("tenants.active")}
                      </span>
                      <span>
                        {t("finance.ctxBalance")}:<b>{formatMoney(ctx.data.balance)}</b>
                      </span>
                      <span>
                        {t("finance.ctxRunning", { count: ctx.data.running_instances })}
                      </span>
                    </Space>
                  }
                  description={
                    ctx.data.recent_ledger.length > 0 ? (
                      <Space orientation="vertical" size={2} style={{ width: "100%" }}>
                        {ctx.data.recent_ledger.map((l) => (
                          <span key={l.id} style={{ fontSize: 12 }}>
                            {formatDateTime(l.created_at)} ·{" "}
                            {(() => {
                              const m = metaOf(ledgerTypeMap, l.type);
                              return m ? t(m.labelKey) : l.type;
                            })()}{" "}
                            · {formatMoney(l.amount)}
                            {l.remark ? ` · ${l.remark}` : ""}
                          </span>
                        ))}
                      </Space>
                    ) : undefined
                  }
                />
              )}
            </div>
          )}
          <Form.Item
            name="amount"
            label={t("finance.amountLabel")}
            rules={[{ required: true }]}
          >
            {/* stringMode:调账金额直接以字符串提交,不经二进制浮点;上限与后端 ADJUST_MAX_ABS 对齐 */}
            <InputNumber
              step="0.01"
              precision={2}
              stringMode
              min={String(-ADJUST_MAX_ABS)}
              max={String(ADJUST_MAX_ABS)}
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
                {r.order_no ?? <TenantLink id={r.user_id} />}
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
