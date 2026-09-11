import { WarningOutlined } from "@ant-design/icons";
import { addAmounts, adjustmentStatusMap, adminColors, fontSize, formatDateTime, idemKeyOf, invoiceStatusMap, ledgerTypeMap, metaOf, orderStatusMap, payoutChannelMap, refundStatusMap } from "@superdl/ui";
import { DataErrorAlert, moneyOr, HexTag, LoadMore, PageContainer, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
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
  exportAdjustmentsCsv,
  exportInvoicesCsv,
  exportOrdersCsv,
  exportReconciliationCsv,
  exportRefundsCsv,
  isApiError,
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
import { ReasonAction } from "../../components/ReasonAction";
import { useApiErrorText } from "@superdl/ui";
import { useCsvExport, useFormDraft } from "@superdl/ui";
import { useFormat } from "@superdl/ui";
import { isValidReason, REASON_MAX_LEN } from "../../lib/validators";
import { AuditTable } from "../../components/AuditTable";
import { useOrderColumns } from "../../components/orderColumns";
import { RowActionModal } from "../../components/RowActionModal";
import { SignedAmount } from "../../components/SignedAmount";
import { TenantLink, tenantColumn } from "../../components/TenantLink";
import { canReadInvoices, canWriteFinance, useAdminRole, useAuth } from "../../stores/auth";
import { SettlementGapsTab } from "./-SettlementGapsTab";

const FINANCE_TABS = ["orders", "refunds", "invoices", "adjustments", "gaps", "anomalies", "audit"] as const;
type FinanceTab = (typeof FINANCE_TABS)[number];

// URL 筛选白名单:状态取共享映射表,日期 YYYY-MM-DD,账期 YYYY-MM,租户 id 正整数
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const PERIOD_RE = /^\d{4}-\d{2}$/;

interface FinanceSearch {
  tab?: FinanceTab;
  // 订单 Tab
  o_status?: string;
  o_no?: string;
  o_day?: string;
  // 退款 Tab
  r_status?: string;
  r_day?: string;
  r_channel?: string;
  // 发票 Tab
  i_status?: string;
  i_period?: string;
  // 调账 Tab
  a_status?: string;
  a_day?: string;
  a_uid?: number;
}

export const Route = createFileRoute("/_app/finance")({
  // 筛选条件入 URL,非法值剥离
  validateSearch: (search: Record<string, unknown>): FinanceSearch => ({
    tab: FINANCE_TABS.includes(search.tab as FinanceTab) ? (search.tab as FinanceTab) : undefined,
    o_status:
      typeof search.o_status === "string" && search.o_status in orderStatusMap
        ? search.o_status
        : undefined,
    o_no: typeof search.o_no === "string" && search.o_no ? search.o_no : undefined,
    o_day:
      typeof search.o_day === "string" && DAY_RE.test(search.o_day) ? search.o_day : undefined,
    r_status:
      typeof search.r_status === "string" && search.r_status in refundStatusMap
        ? search.r_status
        : undefined,
    r_day:
      typeof search.r_day === "string" && DAY_RE.test(search.r_day) ? search.r_day : undefined,
    r_channel:
      typeof search.r_channel === "string" && search.r_channel in payoutChannelMap
        ? search.r_channel
        : undefined,
    i_status:
      typeof search.i_status === "string" && search.i_status in invoiceStatusMap
        ? search.i_status
        : undefined,
    i_period:
      typeof search.i_period === "string" && PERIOD_RE.test(search.i_period)
        ? search.i_period
        : undefined,
    a_status:
      typeof search.a_status === "string" && search.a_status in adjustmentStatusMap
        ? search.a_status
        : undefined,
    a_day:
      typeof search.a_day === "string" && DAY_RE.test(search.a_day) ? search.a_day : undefined,
    a_uid:
      typeof search.a_uid === "number" && Number.isInteger(search.a_uid) && search.a_uid > 0
        ? search.a_uid
        : typeof search.a_uid === "string" && /^\d+$/.test(search.a_uid)
          ? Number(search.a_uid)
          : undefined,
  }),
  component: FinancePage,
});

/** 各 Tab 共用的 URL 筛选读写(replace,保留他项)。 */
function useFinanceFilters() {
  const navigate = useNavigate({ from: "/finance" });
  const search = Route.useSearch();
  const setFilters = (next: Partial<FinanceSearch>) =>
    void navigate({
      to: "/finance",
      replace: true,
      search: (prev) => ({ ...prev, ...next }),
    });
  return { search, setFilters };
}

// 对账 diff 标红阈值(%)
const RECONCILE_DIFF_WARN_PCT = 2;

function ReconciliationCard() {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const [day, setDay] = useState<Dayjs>(dayjs());
  const { data: report, isError, refetch } = useReconciliation(day.format("YYYY-MM-DD"));
  const diffHigh = report != null && report.diff_pct > RECONCILE_DIFF_WARN_PCT;
  const { doExport, exporting } = useCsvExport((_tz, lang) =>
    exportReconciliationCsv(day.format("YYYY-MM-DD"), lang),
  );

  return (
    <Card
      title={t("finance.reconTitle")}
      extra={
        <Space>
          <DatePicker
            value={day}
            onChange={(d) => d && setDay(d)}
            allowClear={false}
            disabledDate={(d) => d.isAfter(dayjs(), "day")}
          />
          <Button onClick={() => void doExport()} loading={exporting}>
            {t("common.exportCsv")}
          </Button>
        </Space>
      }
    >
      {isError && (
        <DataErrorAlert
          style={{ marginBottom: 12 }}
          title={t("common.loadFailed", { ns: "shared" })}
          description={null}
          onRetry={() => void refetch()}
        />
      )}
      <Row gutter={16}>
        <Col xs={24} sm={12} md={8}>
          <Statistic title={t("finance.billedTotal")} value={moneyOr(formatMoney(report?.billed_total), report != null)} />
        </Col>
        <Col xs={24} sm={12} md={8}>
          <Statistic title={t("finance.estimatedTotal")} value={moneyOr(formatMoney(report?.estimated_total), report != null)} />
        </Col>
        <Col xs={24} sm={12} md={8}>
          <Statistic
            title="diff%"
            value={report ? report.diff_pct : "—"}
            suffix={report ? "%" : undefined}
            styles={{
              content:
                report == null
                  ? undefined
                  : diffHigh
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
  const orderColumns = useOrderColumns({ withTenant: true });
  // 筛选条件入 URL(status/订单号/下单日)
  const { search, setFilters } = useFinanceFilters();
  const status = search.o_status;
  const orderNo = search.o_no ?? "";
  // 检索 commit 制:回车/点搜索/清空才写 URL;URL 回流走渲染期派生态
  const [orderNoInput, setOrderNoInput] = useState(orderNo);
  const [prevOrderNo, setPrevOrderNo] = useState(orderNo);
  if (orderNo !== prevOrderNo) {
    setPrevOrderNo(orderNo);
    setOrderNoInput(orderNo);
  }
  const day = search.o_day ? dayjs(search.o_day) : null;
  const params = {
    ...(status ? { status } : {}),
    ...(orderNo ? { order_no: orderNo } : {}),
    ...(search.o_day ? { day: search.o_day } : {}),
  };
  const q = useOrders(params);
  const orders: OrderRow[] = q.data?.pages.flatMap((p) => p.items) ?? [];
  const { doExport, exporting } = useCsvExport((tz, lang) => exportOrdersCsv(params, tz, lang));
  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
      <Select
        allowClear
        placeholder={t("common.statusFilter")}
        style={{ width: 160 }}
        value={status}
        onChange={(v) => setFilters({ o_status: v })}
        options={Object.entries(orderStatusMap).map(([v, m]) => ({ value: v, label: t(m.labelKey) }))}
      />
      <Input.Search
        allowClear
        placeholder={t("finance.searchOrderPlaceholder")}
        style={{ width: 260 }}
        value={orderNoInput}
        onChange={(e) => setOrderNoInput(e.target.value)}
        onSearch={(v) => setFilters({ o_no: v.trim() || undefined })}
      />
      <DatePicker
        value={day}
        onChange={(d) => setFilters({ o_day: d ? d.format("YYYY-MM-DD") : undefined })}
        allowClear
      />
      <Button onClick={() => void doExport()} loading={exporting}>
        {t("common.exportCsv")}
      </Button>
      </Space>
      <Table<OrderRow>
        scroll={{ x: 900 }}
        rowKey="order_no"
        dataSource={orders}
        loading={q.isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={q.isError}
              isForbidden={isApiError(q.error) && q.error.status === 403}
              onRetry={() => void q.refetch()}
            />
          ),
        }}
        columns={orderColumns}
      />
      <LoadMore
        hasNextPage={Boolean(q.hasNextPage)}
        loading={q.isFetchingNextPage}
        isError={q.isFetchNextPageError}
        loadedCount={orders.length}
        onLoadMore={() => void q.fetchNextPage()}
      />
    </>
  );
}

// 单笔绝对值上限,与 adminapi/service.ADJUST_MAX_ABS 对齐
const ADJUST_MAX_ABS = 100000;

/** 复核确认框:租户/当前余额/调账后余额/发起人/原因;驳回必填理由(入审计)。 */
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
  const [rejectReason, setRejectReason] = useState("");
  const review = useReviewAdjustment({
    mutation: {
      onSuccess: () => {
        message.success(t("finance.reviewed"));
        setRejectReason("");
        onReviewed();
        onClose();
      },
      onError: (e) => message.error(errText(e, t("finance.reviewFailed"))),
    },
  });
  if (!target) return null;
  const { adj, approve } = target;
  // 事后余额:BigInt 相加(禁浮点)
  const after = ctx.data ? addAmounts(ctx.data.balance, adj.amount) : null;
  return (
    <Modal
      open
      title={approve ? t("finance.approveTitle") : t("finance.rejectTitle")}
      okText={approve ? t("finance.approve") : t("finance.reject")}
      okButtonProps={{
        danger: !approve,
        loading: review.isPending,
        disabled: ctx.isError || (!approve && !isValidReason(rejectReason)),
      }}
      onCancel={onClose}
      onOk={() =>
        review.mutate({
          adjustmentId: adj.id,
          data: approve
            ? { approve: true }
            : { approve: false, comment: rejectReason.trim() },
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
          <SignedAmount value={adj.amount} />
        </Descriptions.Item>
        <Descriptions.Item label={t("finance.ctxBalance")}>
          {ctx.isLoading ? "…" : moneyOr(formatMoney(ctx.data?.balance), ctx.data != null)}
        </Descriptions.Item>
        {approve && (
          <Descriptions.Item label={t("finance.ctxBalanceAfter")}>
            {moneyOr(formatMoney(after), after !== null)}
          </Descriptions.Item>
        )}
        <Descriptions.Item label={t("finance.colCreatedBy")}>#{adj.created_by}</Descriptions.Item>
        <Descriptions.Item label={t("finance.colReason")}>{adj.reason}</Descriptions.Item>
      </Descriptions>
      {!approve && (
        <Form layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            label={t("finance.rejectReasonLabel")}
            required
            validateStatus={rejectReason !== "" && !isValidReason(rejectReason) ? "error" : undefined}
            help={
              rejectReason !== "" && !isValidReason(rejectReason)
                ? t("common.reasonRule")
                : undefined
            }
          >
            <Input.TextArea
              rows={2}
              maxLength={REASON_MAX_LEN}
              showCount
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder={t("finance.rejectReasonPlaceholder")}
            />
          </Form.Item>
        </Form>
      )}
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
  // 筛选条件入 URL(status/发起日/租户 id)
  const { search, setFilters } = useFinanceFilters();
  const status = search.a_status;
  const day = search.a_day ? dayjs(search.a_day) : null;
  // 租户 id commit 制;URL 回流走渲染期派生态
  const [uidInput, setUidInput] = useState<number | null>(search.a_uid ?? null);
  const [prevUid, setPrevUid] = useState(search.a_uid);
  if (search.a_uid !== prevUid) {
    setPrevUid(search.a_uid);
    setUidInput(search.a_uid ?? null);
  }
  const commitUid = () => setFilters({ a_uid: uidInput ?? undefined });
  const [creating, setCreating] = useState(false);
  const [reviewTarget, setReviewTarget] = useState<{ adj: AdjustmentRow; approve: boolean } | null>(null);
  const [form] = Form.useForm<{ user_id: number; amount: string; reason: string }>();
  // 新建草稿(sessionStorage),发起成功后清除
  const draft = useFormDraft<{ user_id: number; amount: string; reason: string }>("adjustment-new");
  const refresh = () => void qc.invalidateQueries({ queryKey });
  const params = {
    ...(status ? { status } : {}),
    ...(search.a_day ? { day: search.a_day } : {}),
    ...(search.a_uid ? { user_id: search.a_uid } : {}),
  };
  const { data, queryKey, isLoading, isError, error, refetch, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } =
    useAdjustments(params);
  const { doExport, exporting } = useCsvExport((tz, lang) => exportAdjustmentsCsv(params, tz, lang));
  const rows: AdjustmentRow[] = data?.pages.flatMap((p) => p.items) ?? [];

  // 输入 user_id 回显租户身份与资金现状;不存在阻止提交
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
          placeholder={t("common.statusFilter")}
          style={{ width: 150 }}
          value={status}
          onChange={(v) => setFilters({ a_status: v })}
          options={Object.entries(adjustmentStatusMap).map(([v, m]) => ({ value: v, label: t(m.labelKey) }))}
        />
        <InputNumber
          min={1}
          precision={0}
          controls={false}
          placeholder={t("finance.filterTenantId")}
          style={{ width: 140 }}
          value={uidInput}
          onChange={(v) => setUidInput(v ?? null)}
          onBlur={commitUid}
          onPressEnter={commitUid}
        />
        <DatePicker
          value={day}
          onChange={(d) => setFilters({ a_day: d ? d.format("YYYY-MM-DD") : undefined })}
          allowClear
        />
        <Button onClick={() => void doExport()} loading={exporting}>
          {t("common.exportCsv")}
        </Button>
        <Tooltip title={writable ? "" : t("finance.financeOnlyCreate")}>
          <Button
            type="primary"
            disabled={!writable}
            onClick={() => {
              setCreating(true);
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
          { title: t("finance.colAdjustId"), dataIndex: "id", width: 70 },
          tenantColumn(t("finance.colTenant")),
          {
            title: t("finance.colAmount"),
            dataIndex: "amount",
            render: (v: string) => <SignedAmount value={v} />,
          },
          { title: t("finance.colReason"), dataIndex: "reason", ellipsis: true },
          {
            title: t("finance.colStatus"),
            dataIndex: "status",
            render: (v: string) => {
              const m = metaOf(adjustmentStatusMap, v);
              return <HexTag color={m?.color}>{m ? t(m.labelKey) : v}</HexTag>;
            },
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
      <LoadMore
        hasNextPage={Boolean(hasNextPage)}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void fetchNextPage()}
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
          // 上下文未确认(不存在/查询失败)阻止提交
          if (!ctx.data) return;
          create.mutate({
            data: {
              user_id: values.user_id,
              amount: values.amount,
              reason: values.reason,
            },
            // 幂等键从表单快照派生,失败不轮换
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
                          <span key={l.id} style={{ fontSize: fontSize.caption }}>
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
            {/* stringMode:金额以字符串提交;上限与后端 ADJUST_MAX_ABS 对齐 */}
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
            render: (v: AnomalyRow["kind"]) => (
              <Tag color={ANOMALY_META[v].color}>{t(ANOMALY_META[v].labelKey)}</Tag>
            ),
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

/** 登记打款弹窗:渠道 + 凭证号;出金只发生在这里。 */
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

function RefundsTab() {
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
          { title: t("finance.colRefundNo"), dataIndex: "refund_no", width: 130 },
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

/** 开票弹窗:填发票号;提交即站内信通知。 */
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
  const { formatMoney } = useFormat();
  const [form] = Form.useForm<{ invoice_no: string }>();
  const issue = useIssueInvoice();
  if (!target) return null;
  return (
    <RowActionModal
      title={t("finance.invoiceIssueTitle")}
      okText={t("finance.invoiceIssue")}
      note={t("finance.invoiceIssueNote", {
        period: target.period,
        amount: formatMoney(target.amount),
        title: target.title,
        email: target.email,
      })}
      form={form}
      submit={(values) => issue.mutateAsync({ invoiceId: target.id, data: values })}
      successText={t("finance.invoiceIssued")}
      failText={t("finance.invoiceIssueFailed")}
      onClose={onClose}
      onDone={onDone}
    >
      <Form.Item
        name="invoice_no"
        label={t("finance.invoiceNoLabel")}
        rules={[{ required: true, min: 2, message: t("finance.invoiceNoRule") }]}
      >
        <Input placeholder={t("finance.invoiceNoPlaceholder")} maxLength={64} />
      </Form.Item>
    </RowActionModal>
  );
}

function InvoicesTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const role = useAdminRole();
  const writable = canWriteFinance(role);
  const qc = useQueryClient();
  // 筛选条件入 URL(status/账期)
  const { search, setFilters } = useFinanceFilters();
  const status = search.i_status;
  const urlPeriod = search.i_period ?? "";
  // 账期 commit 制;URL 回流走渲染期派生态
  const [periodInput, setPeriodInput] = useState(urlPeriod);
  const [prevPeriod, setPrevPeriod] = useState(urlPeriod);
  if (urlPeriod !== prevPeriod) {
    setPrevPeriod(urlPeriod);
    setPeriodInput(urlPeriod);
  }
  // 非 YYYY-MM 标红提示,不阻止提交
  const periodBad = periodInput.trim() !== "" && !PERIOD_RE.test(periodInput.trim());
  // 抬头与邮箱默认脱敏;reveal=true + 必填事由回明文,授权绑定当时筛选口径,换筛选即删授权
  const filterKey = `${status ?? ""}|${urlPeriod}`;
  const [reveal, setReveal] = useState<{ reason: string; filterKey: string } | null>(null);
  const [prevFilterKey, setPrevFilterKey] = useState(filterKey);
  if (filterKey !== prevFilterKey) {
    setPrevFilterKey(filterKey);
    setReveal(null);
  }
  const revealReason = reveal?.filterKey === filterKey ? reveal.reason : null;
  const [revealOpen, setRevealOpen] = useState(false);
  const [reasonInput, setReasonInput] = useState("");
  const params = {
    ...(status ? { status } : {}),
    ...(urlPeriod ? { period: urlPeriod } : {}),
    ...(revealReason !== null ? { reveal: true, reason: revealReason } : {}),
  };
  const { data, queryKey, isLoading, isError, error, refetch } = useInvoices(params);
  const { doExport, exporting } = useCsvExport((tz, lang) => exportInvoicesCsv(params, tz, lang));
  const rows: InvoiceRow[] = data ?? [];
  const [issueTarget, setIssueTarget] = useState<InvoiceRow | null>(null);
  const reject = useRejectInvoice();
  const refresh = () => void qc.invalidateQueries({ queryKey });
  const noPerm = t("finance.financeOnlyInvoice");

  return (
    <>
      <Space wrap style={{ marginBottom: 12 }} align="start">
        <Select
          allowClear
          placeholder={t("common.statusFilter")}
          style={{ width: 150 }}
          value={status}
          onChange={(v) => setFilters({ i_status: v })}
          options={Object.entries(invoiceStatusMap).map(([v, m]) => ({
            value: v,
            label: t(m.labelKey),
          }))}
        />
        <Form.Item
          style={{ marginBottom: 0 }}
          validateStatus={periodBad ? "error" : undefined}
          help={periodBad ? t("finance.periodFormatHint") : undefined}
        >
          <Input.Search
            allowClear
            placeholder={t("finance.filterPeriod")}
            style={{ width: 200 }}
            value={periodInput}
            onChange={(e) => setPeriodInput(e.target.value)}
            onSearch={(v) => setFilters({ i_period: v.trim() || undefined })}
          />
        </Form.Item>
        <Tooltip title={revealReason !== null ? t("finance.exportRevealNote") : ""}>
          <Button onClick={() => void doExport()} loading={exporting}>
            {t("common.exportCsv")}
          </Button>
        </Tooltip>
        {revealReason === null ? (
          <Button onClick={() => setRevealOpen(true)}>{t("finance.revealIdentity")}</Button>
        ) : (
          <Tag color="orange" closable onClose={() => setReveal(null)}>
            {t("finance.revealActive", { reason: revealReason })}
          </Tag>
        )}
      </Space>
      <Modal
        title={t("finance.revealTitle")}
        open={revealOpen}
        onCancel={() => setRevealOpen(false)}
        okText={t("finance.revealConfirm")}
        okButtonProps={{ disabled: !isValidReason(reasonInput) }}
        onOk={() => {
          setReveal({ reason: reasonInput.trim(), filterKey });
          setRevealOpen(false);
          setReasonInput("");
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Text type="secondary">{t("finance.revealHint")}</Typography.Text>
          <Input.TextArea
            rows={2}
            value={reasonInput}
            onChange={(e) => setReasonInput(e.target.value)}
            placeholder={t("finance.revealReasonPlaceholder")}
            maxLength={REASON_MAX_LEN}
            showCount
          />
        </Space>
      </Modal>
      <Table<InvoiceRow>
        scroll={{ x: 1100 }}
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
          { title: t("finance.colPeriod"), dataIndex: "period", width: 90 },
          tenantColumn(t("finance.colTenant")),
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
                <div style={{ color: adminColors.textMuted, fontSize: fontSize.caption }}>
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
              return <HexTag color={m?.color}>{m ? t(m.labelKey) : v}</HexTag>;
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
                    <div style={{ color: adminColors.textMuted, fontSize: fontSize.caption }}>
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
  const { t } = useTranslation(["admin", "shared"]);
  const navigate = useNavigate();
  const tab = Route.useSearch({ select: (s) => s.tab });
  const role = useAdminRole();
  const anomaliesQ = useAnomalies();
  const { data: anomalies, isError: anomaliesError } = anomaliesQ;
  const anomalyCount = anomalies?.length ?? 0;
  // 发票 Tab 只给 finance/admin;无权直达 ?tab=invoices 回落订单 Tab 并明示原因
  const showInvoices = canReadInvoices(role);
  const activeTab = tab ?? "orders";
  const invoicesDenied = activeTab === "invoices" && !showInvoices;
  return (
    <PageContainer title={t("menu.finance")}>
      <ReconciliationCard />
      <Card style={{ marginTop: 16 }}>
        {invoicesDenied && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            title={t("finance.tabInvoicesDenied")}
          />
        )}
        <Tabs
          activeKey={invoicesDenied ? "orders" : activeTab}
          onChange={(key) =>
            void navigate({
              to: "/finance",
              replace: true,
              search: key === "orders" ? {} : { tab: key as FinanceTab },
            })
          }
          items={[
            { key: "orders", label: t("finance.tabOrders"), children: <OrdersTab /> },
            { key: "refunds", label: t("finance.tabRefunds"), children: <RefundsTab /> },
            ...(showInvoices
              ? [{ key: "invoices", label: t("finance.tabInvoices"), children: <InvoicesTab /> }]
              : []),
            { key: "adjustments", label: t("finance.tabAdjustments"), children: <AdjustmentsTab /> },
            { key: "gaps", label: t("finance.tabSettlementGaps"), children: <SettlementGapsTab /> },
            {
              key: "anomalies",
              label: (
                <Space size={6}>
                  {t("finance.tabAnomalies")}
                  {/* 计数查询失败显示警示图标,不静默为 0 */}
                  {anomaliesError ? (
                    <Tooltip title={t("common.loadFailed", { ns: "shared" })}>
                      <WarningOutlined style={{ color: adminColors.alertAccent }} />
                    </Tooltip>
                  ) : (
                    anomalyCount > 0 && <Tag color="red">{anomalyCount}</Tag>
                  )}
                </Space>
              ),
              children: <AnomaliesTab />,
            },
            { key: "audit", label: t("menu.audit"), children: <AuditTable /> },
          ]}
        />
      </Card>
    </PageContainer>
  );
}
