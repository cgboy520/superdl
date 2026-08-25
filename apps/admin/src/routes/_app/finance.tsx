import { addAmounts, adminColors, formatDateTime, idemKeyOf, invoiceStatusMap, ledgerTypeMap, metaOf, orderStatusMap, paymentChannelMap, payoutChannelMap, refundStatusMap } from "@superdl/ui";
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
  type InvoiceRow,
  type OrderRow,
  type ReconciliationReport,
  type RefundPayout,
  type RefundRow,
  exportOrdersCsv,
  exportReconciliationCsv,
  useAdjustContext,
  useAdjustments,
  useAnomalies,
  useBackfillOrder,
  useCancelRefund,
  useCreateAdjustment,
  useInvoices,
  useIssueInvoice,
  useOrders,
  usePayoutRefund,
  useReconciliation,
  useRefunds,
  useRejectInvoice,
  useReviewAdjustment,
  useReviewRefund,
  useVerifyOrder,
} from "../../api";
import { LIST_CAPS, ListCapNote } from "../../components/ListCapNote";
import { LoadMoreButton } from "../../components/LoadMore";
import { ReasonAction } from "../../components/ReasonAction";
import { useApiErrorText } from "../../lib/apiError";
import { useCsvExport } from "../../lib/csvExport";
import { useFormDraft } from "../../lib/formDraft";
import { useFormat } from "../../lib/format";
import { AuditTable } from "../../components/AuditTable";
import { StatusTag } from "../../components/StatusTag";
import { TenantLink } from "../../components/TenantLink";
import { canWriteFinance, useAdminRole, useAuth } from "../../stores/auth";
import { SettlementGapsTab } from "./-SettlementGapsTab";

export const Route = createFileRoute("/_app/finance")({
  component: FinancePage,
});

function ReconciliationCard() {
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const [day, setDay] = useState<Dayjs>(dayjs());
  const { data: report } = useReconciliation(day.format("YYYY-MM-DD"));
  const diffHigh = report != null && report.diff_pct > 2;
  const { doExport, exporting } = useCsvExport((_tz, lang) =>
    exportReconciliationCsv(day.format("YYYY-MM-DD"), lang),
  );

  return (
    <Card
      title={t("finance.reconTitle")}
      extra={
        <Space>
          <DatePicker value={day} onChange={(d) => d && setDay(d)} allowClear={false} />
          <Button onClick={() => void doExport()} loading={exporting}>
            {t("common.exportCsv")}
          </Button>
        </Space>
      }
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
  const [day, setDay] = useState<Dayjs | null>(null);
  const params = {
    ...(status ? { status } : {}),
    ...(orderNo ? { order_no: orderNo } : {}),
    ...(day ? { day: day.format("YYYY-MM-DD") } : {}),
  };
  const q = useOrders(params);
  const orders: OrderRow[] = q.data?.pages.flatMap((p) => p.items) ?? [];
  const { doExport, exporting } = useCsvExport((tz, lang) => exportOrdersCsv(params, tz, lang));
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
      <DatePicker value={day} onChange={(d) => setDay(d)} allowClear />
      <Button onClick={() => void doExport()} loading={exporting}>
        {t("common.exportCsv")}
      </Button>
      </Space>
      <Table<OrderRow>
        scroll={{ x: 900 }}
        rowKey="order_no"
        dataSource={orders}
        loading={q.isLoading}
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
      <LoadMoreButton
        visible={Boolean(q.hasNextPage)}
        loading={q.isFetchingNextPage}
        onClick={() => void q.fetchNextPage()}
      />
    </>
  );
}

// 与 adminapi/service.ADJUST_MAX_ABS 对齐:单笔绝对值上限,超出走对公/线下流程
const ADJUST_MAX_ABS = 100000;

/** 复核确认框:列出租户/当前余额/调账后余额/发起人/原因。 */
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
      // 统一走 message_key 目录映射,不直接展示 e.message
      onError: (e) => message.error(errText(e, t("finance.reviewFailed"))),
    },
  });
  if (!target) return null;
  const { adj, approve } = target;
  // 复核预览的事后余额:BigInt 分级精确相加(禁浮点),与服务端 Numeric(14,2) 同口径
  const after = ctx.data ? addAmounts(ctx.data.balance, adj.amount) : null;
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
            {after === null ? "—" : formatMoney(after)}
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
  const [status, setStatus] = useState<string | undefined>();
  const [day, setDay] = useState<Dayjs | null>(null);
  const [userId, setUserId] = useState<number | null>(null);
  const { data, queryKey, isLoading, hasNextPage, isFetchingNextPage, fetchNextPage } =
    useAdjustments({
      ...(status ? { status } : {}),
      ...(day ? { day: day.format("YYYY-MM-DD") } : {}),
      ...(userId ? { user_id: userId } : {}),
    });
  const rows: AdjustmentRow[] = data?.pages.flatMap((p) => p.items) ?? [];
  const [creating, setCreating] = useState(false);
  const [reviewTarget, setReviewTarget] = useState<{ adj: AdjustmentRow; approve: boolean } | null>(null);
  const [form] = Form.useForm<{ user_id: number; amount: string; reason: string }>();
  // 新建草稿(sessionStorage):误关弹窗不丢;发起成功后清除
  const draft = useFormDraft<{ user_id: number; amount: string; reason: string }>("adjustment-new");
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
        draft.clear();
        refresh();
      },
      onError: (e) => message.error(errText(e, t("finance.createFailed"))),
    },
  });

  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder={t("tenants.statusFilter")}
          style={{ width: 150 }}
          value={status}
          onChange={setStatus}
          options={[
            { value: "pending", label: t("finance.adjustPending") },
            { value: "approved", label: t("finance.adjustApproved") },
            { value: "rejected", label: t("finance.adjustRejected") },
          ]}
        />
        <InputNumber
          min={1}
          precision={0}
          placeholder={t("finance.filterTenantId")}
          style={{ width: 140 }}
          value={userId}
          onChange={(v) => setUserId(v ?? null)}
        />
        <DatePicker value={day} onChange={(d) => setDay(d)} allowClear />
        <Tooltip title={writable ? "" : t("finance.financeOnlyCreate")}>
          <Button
            type="primary"
            disabled={!writable}
            onClick={() => {
              setCreating(true);
              // 打开时复活草稿(若有),让误关的未提交内容回来
              const d = draft.load();
              if (d) form.setFieldsValue(d);
            }}
          >
            {t("finance.createAdjust")}
          </Button>
        </Tooltip>
      </Space>
      <Table<AdjustmentRow>
        scroll={{ x: 1000 }}
        rowKey="id"
        loading={isLoading}
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
          { title: t("finance.colReason"), dataIndex: "reason", ellipsis: true },
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
      <LoadMoreButton
        visible={Boolean(hasNextPage)}
        loading={isFetchingNextPage}
        onClick={() => void fetchNextPage()}
      />
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
            // 幂等键从表单快照派生、失败不轮换:双击/重试安全重放,
            // 改掉任一字段再提交才是真正的新调账(后端有唯一约束兜底)
            idempotencyKey: idemKeyOf("adj", [values.user_id, values.amount, values.reason]),
          });
        }}
        okButtonProps={{ loading: create.isPending, disabled: ctxId === null || !ctx.data }}
      >
        <Form
          form={form}
          layout="vertical"
          onValuesChange={() => draft.save(form.getFieldsValue(true))}
        >
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
  failed_order: { labelKey: "finance.anomalyFailedOrder", color: "volcano" },
  channel_reversed: { labelKey: "finance.anomalyChannelReversed", color: "magenta" },
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
              // 同上调账:快照派生幂等键,重试不重复入账(后端唯一约束兜底)
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

/** 登记打款弹窗:渠道下拉 + 凭证号。出金只发生在这里(审批通过不动钱包)。 */
function PayoutModal({
  target,
  onClose,
  onDone,
}: {
  target: RefundRow | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { formatMoney } = useFormat();
  const { message } = App.useApp();
  const [form] = Form.useForm<RefundPayout>();
  const payout = usePayoutRefund();
  if (!target) return null;
  return (
    <Modal
      open
      title={t("finance.payoutTitle", { no: target.refund_no })}
      okText={t("finance.payoutOk")}
      okButtonProps={{ loading: payout.isPending }}
      onCancel={onClose}
      onOk={async () => {
        const values = await form.validateFields();
        try {
          await payout.mutateAsync({ refundId: target.id, data: values });
          message.success(t("finance.payoutDone"));
          onDone();
          onClose();
        } catch (e) {
          message.error(errText(e, t("finance.payoutFailed")));
        }
      }}
    >
      <Space orientation="vertical" size={12} style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          title={t("finance.payoutNote", {
            amount: formatMoney(target.amount),
            reviewer: `#${target.review_by ?? "-"}`,
          })}
        />
        <Form form={form} layout="vertical">
          <Form.Item
            name="channel"
            label={t("finance.payoutChannelLabel")}
            rules={[{ required: true }]}
          >
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
        </Form>
      </Space>
    </Modal>
  );
}

function RefundsTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const role = useAdminRole();
  const { admin } = useAuth();
  const writable = canWriteFinance(role);
  const qc = useQueryClient();
  const [status, setStatus] = useState<string | undefined>();
  const [day, setDay] = useState<Dayjs | null>(null);
  // 渠道过滤在客户端做(接口口径只有 status/day;对已加载页生效)
  const [channel, setChannel] = useState<string | undefined>();
  const { data, queryKey, isLoading, hasNextPage, isFetchingNextPage, fetchNextPage } = useRefunds({
    ...(status ? { status } : {}),
    ...(day ? { day: day.format("YYYY-MM-DD") } : {}),
  });
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
          placeholder={t("tenants.statusFilter")}
          style={{ width: 150 }}
          value={status}
          onChange={setStatus}
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
          onChange={setChannel}
          options={Object.entries(payoutChannelMap).map(([v, m]) => ({
            value: v,
            label: t(m.labelKey),
          }))}
        />
        <DatePicker value={day} onChange={(d) => setDay(d)} allowClear />
      </Space>
      <Table<RefundRow>
        scroll={{ x: 1100 }}
        rowKey="id"
        loading={isLoading}
        dataSource={rows}
        columns={[
          { title: t("finance.colRefundNo"), dataIndex: "refund_no", width: 130 },
          {
            title: t("finance.colTenant"),
            dataIndex: "user_id",
            width: 80,
            render: (v: number) => <TenantLink id={v} />,
          },
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
              return <StatusTag color={m?.color}>{m ? t(m.labelKey) : v}</StatusTag>;
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
                  <div style={{ color: adminColors.textMuted, fontSize: 12 }}>
                    #{r.payout_by} · {r.payout_at ? formatDateTime(r.payout_at) : ""}
                  </div>
                </span>
              );
            },
          },
          {
            title: t("finance.colAction"),
            width: 230,
            render: (_, r) => {
              if (r.status !== "pending" && r.status !== "approved") return null;
              const isReviewer = admin?.id === r.review_by;
              return (
                <Space wrap>
                  {r.status === "pending" && (
                    <>
                      <ReasonAction
                        label={t("finance.refundApprove")}
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
                        title={t("finance.refundRejectTitle")}
                        confirmText={t("finance.refundRejectConfirm")}
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
                  <ReasonAction
                    label={t("finance.cancelRefund")}
                    title={t("finance.cancelRefundTitle")}
                    confirmText={t("finance.cancelRefundConfirm")}
                    danger
                    disabled={!writable}
                    disabledReason={noPerm}
                    onSubmit={async (reason) => {
                      await cancel.mutateAsync({ refundId: r.id, data: { reason } });
                      refresh();
                    }}
                  />
                </Space>
              );
            },
          },
          { title: t("finance.colCreatedAt"), dataIndex: "created_at", width: 150, render: formatDateTime },
        ]}
      />
      <LoadMoreButton
        visible={Boolean(hasNextPage)}
        loading={isFetchingNextPage}
        onClick={() => void fetchNextPage()}
      />
      <PayoutModal
        target={payoutTarget}
        onClose={() => setPayoutTarget(null)}
        onDone={refresh}
      />
    </>
  );
}

/** 开票弹窗:填发票号(人工开票,发票经邮箱送达用户;提交即站内信通知)。 */
function IssueInvoiceModal({
  target,
  onClose,
  onDone,
}: {
  target: InvoiceRow | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { formatMoney } = useFormat();
  const { message } = App.useApp();
  const [form] = Form.useForm<{ invoice_no: string }>();
  const issue = useIssueInvoice();
  if (!target) return null;
  return (
    <Modal
      open
      title={t("finance.invoiceIssueTitle")}
      okText={t("finance.invoiceIssue")}
      okButtonProps={{ loading: issue.isPending }}
      onCancel={onClose}
      onOk={async () => {
        const values = await form.validateFields();
        try {
          await issue.mutateAsync({ invoiceId: target.id, data: values });
          message.success(t("finance.invoiceIssued"));
          onDone();
          onClose();
        } catch (e) {
          message.error(errText(e, t("finance.invoiceIssueFailed")));
        }
      }}
    >
      <Space orientation="vertical" size={12} style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          title={t("finance.invoiceIssueNote", {
            period: target.period,
            amount: formatMoney(target.amount),
            title: target.title,
            email: target.email,
          })}
        />
        <Form form={form} layout="vertical">
          <Form.Item
            name="invoice_no"
            label={t("finance.invoiceNoLabel")}
            rules={[{ required: true, min: 2, message: t("finance.invoiceNoRule") }]}
          >
            <Input placeholder={t("finance.invoiceNoPlaceholder")} maxLength={64} />
          </Form.Item>
        </Form>
      </Space>
    </Modal>
  );
}

function InvoicesTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const role = useAdminRole();
  const writable = canWriteFinance(role);
  const qc = useQueryClient();
  const [status, setStatus] = useState<string | undefined>();
  const [period, setPeriod] = useState("");
  const { data, queryKey, isLoading } = useInvoices({
    ...(status ? { status } : {}),
    ...(period ? { period } : {}),
  });
  const rows: InvoiceRow[] = data ?? [];
  const [issueTarget, setIssueTarget] = useState<InvoiceRow | null>(null);
  const reject = useRejectInvoice();
  const refresh = () => void qc.invalidateQueries({ queryKey });
  const noPerm = t("finance.financeOnlyInvoice");

  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder={t("tenants.statusFilter")}
          style={{ width: 150 }}
          value={status}
          onChange={setStatus}
          options={Object.entries(invoiceStatusMap).map(([v, m]) => ({
            value: v,
            label: t(m.labelKey),
          }))}
        />
        <Input.Search
          allowClear
          placeholder={t("finance.filterPeriod")}
          style={{ width: 200 }}
          onSearch={setPeriod}
        />
      </Space>
      <Table<InvoiceRow>
        scroll={{ x: 1100 }}
        rowKey="id"
        loading={isLoading}
        dataSource={rows}
        columns={[
          { title: t("finance.colPeriod"), dataIndex: "period", width: 90 },
          {
            title: t("finance.colTenant"),
            dataIndex: "user_id",
            width: 80,
            render: (v: number) => <TenantLink id={v} />,
          },
          {
            title: t("finance.colAmount"),
            dataIndex: "amount",
            width: 100,
            render: (v: string) => formatMoney(v),
          },
          {
            title: t("finance.colTitleInfo"),
            ellipsis: true,
            render: (_, r) => (
              <>
                {r.title}
                <div style={{ color: adminColors.textMuted, fontSize: 12 }}>
                  {r.title_type === "company"
                    ? t("finance.invoiceTitleTypeCompany")
                    : t("finance.invoiceTitleTypePersonal")}
                  {r.tax_id ? ` · ${r.tax_id}` : ""}
                </div>
              </>
            ),
          },
          { title: t("finance.colEmail"), dataIndex: "email", width: 180, ellipsis: true },
          {
            title: t("finance.colStatus"),
            dataIndex: "status",
            width: 90,
            render: (v: string) => {
              const m = metaOf(invoiceStatusMap, v);
              return <StatusTag color={m?.color}>{m ? t(m.labelKey) : v}</StatusTag>;
            },
          },
          {
            title: t("finance.colInvoiceInfo"),
            width: 190,
            render: (_, r) => {
              if (r.status === "issued") {
                return (
                  <span>
                    {r.invoice_no}
                    <div style={{ color: adminColors.textMuted, fontSize: 12 }}>
                      #{r.issued_by} · {r.issued_at ? formatDateTime(r.issued_at) : ""}
                    </div>
                  </span>
                );
              }
              if (r.status === "rejected") {
                return <span style={{ color: adminColors.textMuted }}>{r.reject_reason}</span>;
              }
              return "-";
            },
          },
          {
            title: t("finance.colAction"),
            width: 150,
            render: (_, r) => {
              if (r.status !== "submitted") return null;
              return (
                <Space wrap>
                  <Tooltip title={writable ? "" : noPerm}>
                    <Button
                      size="small"
                      type="primary"
                      disabled={!writable}
                      onClick={() => setIssueTarget(r)}
                    >
                      {t("finance.invoiceIssue")}
                    </Button>
                  </Tooltip>
                  <ReasonAction
                    label={t("finance.invoiceReject")}
                    title={t("finance.invoiceRejectTitle")}
                    confirmText={t("finance.invoiceRejectConfirm")}
                    danger
                    disabled={!writable}
                    disabledReason={noPerm}
                    onSubmit={async (reason) => {
                      await reject.mutateAsync({ invoiceId: r.id, data: { reason } });
                      refresh();
                    }}
                  />
                </Space>
              );
            },
          },
          { title: t("finance.colCreatedAt"), dataIndex: "created_at", width: 150, render: formatDateTime },
        ]}
      />
      <ListCapNote rows={rows.length} cap={LIST_CAPS.invoices} />
      <IssueInvoiceModal
        target={issueTarget}
        onClose={() => setIssueTarget(null)}
        onDone={refresh}
      />
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
            { key: "refunds", label: t("finance.tabRefunds"), children: <RefundsTab /> },
            { key: "invoices", label: t("finance.tabInvoices"), children: <InvoicesTab /> },
            { key: "adjustments", label: t("finance.tabAdjustments"), children: <AdjustmentsTab /> },
            { key: "gaps", label: t("finance.tabSettlementGaps"), children: <SettlementGapsTab /> },
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
