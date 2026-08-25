/**
 * 费用中心:余额卡(阈值带保存钮)/充值 Modal(渠道 Tab 预留+真二维码+有效期)/
 * 消费概览(月选择器+今日)环图/账单与收支明细(服务端 CSV 导出)。
 * Tab 与月份入 URL;充值幂等键按 (amount, channel) 派生。
 */

import {
  exportBillingApiV1BillingExportGet,
  type BillHourlyOut,
  type InvoiceEligibleOut,
  type InvoiceOut,
  type LedgerEntryOut,
  type RechargeOut,
  type RefundOut,
  type RefundableOrderOut,
} from "@superdl/api-client";
import { addAmounts, compareAmounts, formatDateTime, idemKeyOf, invoiceStatusMap, localToday, metaOf, payoutChannelMap, refundStatusMap, statusColors } from "@superdl/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import dayjs from "dayjs";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  DatePicker,
  Empty,
  Input,
  InputNumber,
  Modal,
  QRCode,
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useFormat } from "../lib/format";
import EChart from "../components/EChart";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { useCreateInvoice, useCreateRecharge, useCreateRefund, useMockPay, useSetWarnThreshold } from "../api/mutations";
import { DataErrorAlert, moneyOr, TableErrorEmpty } from "../components/QueryState";
import {
  useBillSummary,
  useDailySummary,
  useHourlyBillPages,
  useInvoiceEligible,
  useInvoicePages,
  useLedgerPages,
  useMe,
  usePolicies,
  useRecharge,
  useRefundPages,
  useRefundableOrders,
  useSiteConfig,
  useWallet,
} from "../api/queries";
import { downloadCsv } from "../lib/csv";
import { requireAuth } from "../lib/guard";

const BILLING_TABS = ["bills", "ledger", "refunds", "invoices"] as const;
type BillingTab = (typeof BILLING_TABS)[number];

/** 服务端 CSV 截断标记(与 apps/api billing/export.py TRUNCATED_MARKER 一致)。 */
const TRUNCATED_MARKER = "#SUPERDL_EXPORT_TRUNCATED#";

export const Route = createFileRoute("/_console/billing")({
  beforeLoad: requireAuth,
  // Tab/月份入 URL:可分享、返回不丢;非法值丢弃回默认
  validateSearch: (search: Record<string, unknown>): { tab?: BillingTab; month?: string } => {
    const out: { tab?: BillingTab; month?: string } = {};
    if (typeof search.tab === "string" && (BILLING_TABS as readonly string[]).includes(search.tab)) {
      out.tab = search.tab as BillingTab;
    }
    if (typeof search.month === "string" && /^\d{4}-\d{2}$/.test(search.month)) {
      out.month = search.month;
    }
    return out;
  },
  component: BillingPage,
});

const LEDGER_TYPE = {
  recharge: { labelKey: "billing.ledgerType.recharge", color: "green" },
  consume: { labelKey: "billing.ledgerType.consume", color: "blue" },
  refund: { labelKey: "billing.ledgerType.refund", color: "orange" },
  adjust: { labelKey: "billing.ledgerType.adjust", color: "purple" },
} as const;
type LedgerTypeKey = keyof typeof LEDGER_TYPE;

const PRESET_AMOUNTS = ["50.00", "100.00", "500.00"] as const;

/** 进行中的充值订单号(sessionStorage):支付中途关窗后重开可恢复轮询。 */
const PENDING_ORDER_KEY = "superdl.web.pendingRecharge";

/** 支付倒计时:把到期时刻渲染成剩余时长。 */
function PayCountdown({ expiresAt }: { expiresAt: string }) {
  const { t } = useTranslation();
  const [left, setLeft] = useState(() => Math.max(0, new Date(expiresAt).getTime() - Date.now()));
  useEffect(() => {
    const timer = setInterval(
      () => setLeft(Math.max(0, new Date(expiresAt).getTime() - Date.now())),
      1000,
    );
    return () => clearInterval(timer);
  }, [expiresAt]);
  if (left <= 0) return <span>{t("billing.orderExpired")}</span>;
  const total = Math.floor(left / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  // 订单 TTL 2 小时:超过一小时必须显示时段,否则 "119:58" 会被读成 119 分钟
  const time = h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
  return <span>{t("billing.payCountdown", { time })}</span>;
}

function RechargeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { currencySymbol, formatMoney } = useFormat();
  const { t } = useTranslation();
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  // 金额必须按字符串走(InputNumber stringMode),禁止经二进制浮点
  const [amount, setAmount] = useState("100.00");
  const [order, setOrder] = useState<RechargeOut | null>(null);
  // 幂等键按「下单序号 + (amount, channel)」派生:响应丢失后重提不会再开一单,
  // 改了金额/渠道即另一单;下单成功后序号 +1,下一笔同额同渠道是新订单
  const [orderSeq, setOrderSeq] = useState(0);
  const [pickedChannel, setPickedChannel] = useState<string | null>(null);
  // 中断找回:订单号落 sessionStorage,支付中途关窗/刷新后重开可恢复轮询
  const [resumedNo, setResumedNo] = useState(() => sessionStorage.getItem(PENDING_ORDER_KEY) ?? "");

  // 渠道开关来自管理端·平台配置(site-config 公开端点)
  const siteQ = useSiteConfig();
  const { data: site } = siteQ;
  const enabled = {
    wechat: site?.payment_channels.wechat ?? false,
    alipay: site?.payment_channels.alipay ?? false,
    mock: site?.payment_channels.mock ?? false,
  };
  const firstEnabled = enabled.wechat ? "wechat" : enabled.alipay ? "alipay" : "mock";
  const channel = pickedChannel ?? firstEnabled;
  const anyEnabled = enabled.wechat || enabled.alipay || enabled.mock;
  // mock 渠道关闭(正式环境)时,未开通渠道不再引导用户去找模拟支付
  const channelTip = enabled.mock ? t("copy.channelComingSoon") : t("copy.channelPending");

  const create = useCreateRecharge({
    onSuccess: (d) => {
      const o = d as RechargeOut;
      setOrder(o);
      setResumedNo(o.order_no);
      sessionStorage.setItem(PENDING_ORDER_KEY, o.order_no);
      setOrderSeq((s) => s + 1);
    },
  });
  const mockPay = useMockPay({ onSuccess: () => message.success(t("billing.mockPaySent")) });
  const activeNo = order?.order_no ?? resumedNo;
  const rechargeQ = useRecharge(activeNo, {
    // 轮询仅在弹窗 open 时进行:关窗即停,重开经找回标记恢复
    enabled: open && activeNo !== "",
    // 到终态(paid/closed/failed)即停,不再空转打接口;出错(如找回的单号已失效)也停
    refetchInterval: (q) => {
      if (q.state.status === "error") return false;
      return q.state.data && q.state.data.status !== "pending" ? false : 2_000;
    },
  });
  const { data: polled } = rechargeQ;
  // 找回的单号已失效(关单/账号已切):清找回标记;shown 为 null 自然回表单态
  useEffect(() => {
    if (rechargeQ.isError && !order) sessionStorage.removeItem(PENDING_ORDER_KEY);
  }, [rechargeQ.isError, order]);
  // 恢复的订单没有本地创建快照,轮询结果就是订单本体
  const shown = polled ?? order;
  const status = shown?.status;
  const paid = status === "paid";

  // 到终态即清找回标记;到账定向失效钱包与流水(不再等 10s 轮询)
  useEffect(() => {
    if (!status) return;
    if (status === "paid") {
      sessionStorage.removeItem(PENDING_ORDER_KEY);
      void queryClient.invalidateQueries({ queryKey: ["wallet"] });
      void queryClient.invalidateQueries({ queryKey: ["ledger"] });
    } else if (status !== "pending") {
      sessionStorage.removeItem(PENDING_ORDER_KEY);
    }
  }, [status, queryClient]);

  const reset = () => {
    setOrder(null);
    setResumedNo("");
    onClose();
  };

  return (
    <Modal
      title={t("billing.recharge")}
      open={open}
      onCancel={reset}
      footer={null}
      // 中途关窗保留找回标记;重新打开时若本地无单,从 sessionStorage 再捡回来
      afterOpenChange={(o) => {
        if (o && !order) setResumedNo(sessionStorage.getItem(PENDING_ORDER_KEY) ?? "");
      }}
    >
      {!shown ? (
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          {siteQ.isError && (
            // 渠道信息加载失败绝不伪装成「全部渠道未开通」(两个灰 tab + 死按钮无解释)
            <DataErrorAlert onRetry={() => void siteQ.refetch()} />
          )}
          <Tabs
            activeKey={channel}
            onChange={setPickedChannel}
            size="small"
            items={[
              {
                key: "wechat",
                label: enabled.wechat ? (
                  t("billing.wechat")
                ) : (
                  <Tooltip title={channelTip}>{t("billing.wechat")}</Tooltip>
                ),
                disabled: !enabled.wechat,
              },
              {
                key: "alipay",
                label: enabled.alipay ? (
                  t("billing.alipay")
                ) : (
                  <Tooltip title={channelTip}>{t("billing.alipay")}</Tooltip>
                ),
                disabled: !enabled.alipay,
              },
              ...(enabled.mock
                ? [{ key: "mock", label: t("billing.mockChannel") }]
                : []),
            ]}
          />
          <Radio.Group
            optionType="button"
            value={PRESET_AMOUNTS.find((v) => compareAmounts(v, amount) === 0)}
            onChange={(e) => setAmount(e.target.value as string)}
            options={PRESET_AMOUNTS.map((v) => ({ value: v, label: formatMoney(v) }))}
          />
          <InputNumber
            style={{ width: 200 }}
            min="1"
            max="50000"
            precision={2}
            stringMode
            value={amount}
            onChange={(v) => setAmount(v ?? "0")}
            prefix={currencySymbol}
            aria-label={t("billing.rechargeAmount")}
          />
          <Button
            type="primary"
            block
            disabled={!anyEnabled}
            loading={create.isPending}
            onClick={() =>
              create.mutate({
                body: { amount, channel },
                idempotencyKey: idemKeyOf("recharge", [orderSeq, amount, channel]),
              })
            }
          >
            {t("billing.genQr")}
          </Button>
        </Space>
      ) : paid ? (
        <Space orientation="vertical" align="center" style={{ width: "100%" }}>
          <Typography.Title level={4} type="success">
            {t("billing.paySuccess")}
          </Typography.Title>
          <Typography.Text>{t("billing.credited", { amount: formatMoney(shown.amount) })}</Typography.Text>
          <Button type="primary" onClick={reset}>
            {t("billing.done")}
          </Button>
        </Space>
      ) : (
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            title={t("billing.orderWaiting", {
              no: shown.order_no,
              time: formatDateTime(shown.expires_at),
            })}
            description={
              <PayCountdown expiresAt={shown.expires_at} />
            }
          />
          <div style={{ display: "flex", justifyContent: "center" }}>
            <QRCode value={shown.qr_url ?? shown.order_no} size={168} />
          </div>
          {shown.channel === "wechat" && (
            <Typography.Text type="secondary" style={{ display: "block", textAlign: "center" }}>
              {t("billing.scanWithWechat")}
            </Typography.Text>
          )}
          {shown.channel === "alipay" && (
            <Typography.Text type="secondary" style={{ display: "block", textAlign: "center" }}>
              {t("billing.scanWithAlipay")}
            </Typography.Text>
          )}
          {shown.channel === "mock" && (
            <Button
              block
              loading={mockPay.isPending}
              onClick={() =>
                mockPay.mutate({ order_no: shown.order_no, amount: shown.amount })
              }
            >
              {t("billing.mockPayNow")}
            </Button>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("billing.pollNote")}
          </Typography.Text>
        </Space>
      )}
    </Modal>
  );
}

/** 小时账单:游标分页 + 「加载更多」(与收支明细同构),金额不过 Number。 */
function HourlyBillsTable({ month, tzOffsetMinutes }: { month: string; tzOffsetMinutes: number }) {
  const { t } = useTranslation();
  const { formatDuration, formatHourlyPrice, formatMoney } = useFormat();
  const { data, isLoading, isError, refetch, isFetchingNextPage, hasNextPage, fetchNextPage } =
    useHourlyBillPages({ month, tz_offset_minutes: tzOffsetMinutes });
  const rows = useMemo<BillHourlyOut[]>(() => (data?.pages ?? []).flatMap((p) => p.items), [data]);

  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <Table
        rowKey="id"
        size="small"
        pagination={false}
        scroll={{ x: 760 }}
        loading={isLoading}
        dataSource={rows}
        locale={{
          emptyText: isError ? <TableErrorEmpty onRetry={() => void refetch()} /> : undefined,
        }}
        columns={[
          { title: t("instances.colBillHour"), render: (_, r) => formatDateTime(r.hour_start) },
          { title: t("billing.colInstance"), render: (_, r) => r.instance_name ?? `#${r.instance_id}` },
          { title: t("instances.colBillDuration"), render: (_, r) => formatDuration(r.seconds_used) },
          {
            title: t("instances.colBillUnit"),
            render: (_, r) => (
              <span>
                {formatHourlyPrice(r.unit_price)} × {r.gpu_count}
              </span>
            ),
          },
          {
            title: t("instances.colBillAmount"),
            render: (_, r) => <span>{formatMoney(r.amount)}</span>,
          },
        ]}
      />
      {hasNextPage && (
        <Button block loading={isFetchingNextPage} onClick={() => void fetchNextPage()}>
          {t("billing.loadMore")}
        </Button>
      )}
    </Space>
  );
}

function LedgerTable() {
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const { data, isLoading, isError, refetch, isFetchingNextPage, hasNextPage, fetchNextPage } =
    useLedgerPages(20);
  const merged = useMemo<LedgerEntryOut[]>(
    () => (data?.pages ?? []).flatMap((p) => p.items),
    [data],
  );

  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <Table
        rowKey="id"
        size="small"
        pagination={false}
        scroll={{ x: 760 }}
        loading={isLoading}
        dataSource={merged}
        locale={{
          emptyText: isError ? <TableErrorEmpty onRetry={() => void refetch()} /> : undefined,
        }}
        columns={[
          { title: t("billing.colTime"), render: (_, r) => formatDateTime(r.created_at) },
          {
            title: t("billing.colType"),
            render: (_, r) => {
              const meta = LEDGER_TYPE[r.type as LedgerTypeKey] as (typeof LEDGER_TYPE)[LedgerTypeKey] | undefined;
              return <Tag color={meta?.color ?? "default"}>{meta ? t(meta.labelKey) : r.type}</Tag>;
            },
          },
          {
            title: t("billing.colAmount"),
            render: (_, r) => (
              <span
                style={{ color: r.amount.startsWith("-") ? undefined : statusColors.green }}
              >
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
      {hasNextPage && (
        <Button block loading={isFetchingNextPage} onClick={() => void fetchNextPage()}>
          {t("billing.loadMore")}
        </Button>
      )}
    </Space>
  );
}

/** 退款:申请表单(仅可申请口径的订单) + 我的退款列表。 */
const REFUND_REASON_CODE = {
  not_paid: "billing.refundOrderNotPaid",
  already_applied: "billing.refundOrderAlreadyApplied",
  invoiced: "billing.refundOrderInvoiced",
  no_balance: "billing.refundOrderNoBalance",
} as const;

function refundReasonText(
  o: RefundableOrderOut,
  t: (key: (typeof REFUND_REASON_CODE)[keyof typeof REFUND_REASON_CODE]) => string,
): string {
  const key = o.reason_code as keyof typeof REFUND_REASON_CODE | null;
  return key ? t(REFUND_REASON_CODE[key]) : "";
}

function RefundTab() {
  const { t } = useTranslation(["web", "shared"]);
  const { currencySymbol, formatMoney } = useFormat();
  const { message } = App.useApp();
  const ordersQ = useRefundableOrders();
  const orders = useMemo<RefundableOrderOut[]>(() => ordersQ.data ?? [], [ordersQ.data]);
  const [orderNo, setOrderNo] = useState<string>();
  const [amount, setAmount] = useState("0");
  const [reason, setReason] = useState("");
  // 幂等键按「提交序号 + 表单快照」派生:同一键重放返回既有单(双击/重试安全),成功后序号 +1 即新单
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
          // 加载失败绝不能伪装成「无充值订单」
          <DataErrorAlert onRetry={() => void ordersQ.refetch()} />
        ) : orders.length === 0 && !ordersQ.isLoading ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("billing.refundNoOrders")} />
        ) : (
          <Space orientation="vertical" size={12} style={{ width: "100%" }}>
            <Select
              style={{ width: "100%", maxWidth: 560 }}
              placeholder={t("billing.refundSelectOrder")}
              loading={ordersQ.isLoading}
              value={orderNo}
              onChange={(v: string) => {
                setOrderNo(v);
                // 默认退满上限:min(订单额, 当前余额),与服务端口径一致
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
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
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
              <Button
                type="primary"
                loading={create.isPending}
                disabled={!selected || reason.trim().length < 2}
                onClick={submit}
              >
                {t("billing.refundSubmit")}
              </Button>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
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
            <TableErrorEmpty onRetry={() => void refunds.refetch()} />
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
      {refunds.hasNextPage && (
        <Button
          block
          loading={refunds.isFetchingNextPage}
          onClick={() => void refunds.fetchNextPage()}
        >
          {t("billing.loadMore")}
        </Button>
      )}
    </Space>
  );
}

/** 发票申请弹窗:账期(仅 eligible 列表)+ 抬头信息;金额由服务端按账期计算。 */
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/; // 与服务端契约同一口径

function InvoiceApplyModal({
  periods,
  open,
  onClose,
}: {
  periods: InvoiceEligibleOut[];
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatMoney } = useFormat();
  const { message } = App.useApp();
  const [period, setPeriod] = useState<string>();
  const [titleType, setTitleType] = useState<"personal" | "company">("company");
  const [title, setTitle] = useState("");
  const [taxId, setTaxId] = useState("");
  const [email, setEmail] = useState("");
  // 幂等键按「提交序号 + 表单快照」派生:同一键重放返回既有单(双击/重试安全),成功后序号 +1 即新单
  const [submitSeq, setSubmitSeq] = useState(0);
  const create = useCreateInvoice({
    onSuccess: () => {
      message.success(t("billing.invoiceCreated"));
      setPeriod(undefined);
      setTitle("");
      setTaxId("");
      setEmail("");
      setSubmitSeq((s) => s + 1);
      onClose();
    },
  });

  const emailOk = EMAIL_RE.test(email.trim());
  const canSubmit =
    period != null &&
    title.trim().length >= 2 &&
    emailOk &&
    (titleType === "personal" || taxId.trim().length >= 4);

  return (
    <Modal
      title={t("billing.invoiceApplyTitle")}
      open={open}
      onCancel={onClose}
      okText={t("billing.invoiceSubmit")}
      okButtonProps={{ disabled: !canSubmit, loading: create.isPending }}
      onOk={() => {
        if (!period || !canSubmit) return;
        create.mutate({
          body: {
            period,
            title_type: titleType,
            title: title.trim(),
            tax_id: titleType === "company" ? taxId.trim() : null,
            email: email.trim(),
          },
          idempotencyKey: idemKeyOf("invoice", [
            submitSeq,
            period,
            titleType,
            title.trim(),
            titleType === "company" ? taxId.trim() : null,
            email.trim(),
          ]),
        });
      }}
    >
      <Space orientation="vertical" size={12} style={{ width: "100%" }}>
        <Alert type="info" showIcon title={t("billing.invoiceManualNote")} />
        <Select
          style={{ width: "100%" }}
          placeholder={t("billing.invoicePeriodSelect")}
          value={period}
          onChange={(v: string) => setPeriod(v)}
          options={periods.map((p) => ({
            value: p.period,
            label: `${p.period} · ${formatMoney(p.amount)}`,
          }))}
        />
        <Radio.Group
          optionType="button"
          value={titleType}
          onChange={(e) => setTitleType(e.target.value as "personal" | "company")}
          options={[
            { value: "company", label: t("billing.invoiceTitleTypeCompany") },
            { value: "personal", label: t("billing.invoiceTitleTypePersonal") },
          ]}
        />
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={t("billing.invoiceTitlePlaceholder")}
          maxLength={128}
          aria-label={t("billing.invoiceTitleLabel")}
        />
        {titleType === "company" && (
          <Input
            value={taxId}
            onChange={(e) => setTaxId(e.target.value)}
            placeholder={t("billing.invoiceTaxIdPlaceholder")}
            maxLength={32}
            aria-label={t("billing.invoiceTaxId")}
          />
        )}
        <Input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder={t("billing.invoiceEmailPlaceholder")}
          maxLength={128}
          status={email !== "" && !emailOk ? "error" : undefined}
          aria-label={t("billing.invoiceEmail")}
        />
      </Space>
    </Modal>
  );
}

/** 发票:可开票额度卡片(总额 + 各账期明细) + 申请弹窗 + 我的发票列表。 */
function InvoiceTab() {
  const { t } = useTranslation(["web", "shared"]);
  const { formatMoney } = useFormat();
  const eligibleQ = useInvoiceEligible();
  const periods = useMemo<InvoiceEligibleOut[]>(() => eligibleQ.data ?? [], [eligibleQ.data]);
  // 总额不过 Number:逐账期字符串相加(2 位小数)
  const total = useMemo(
    () => periods.reduce((acc, p) => addAmounts(acc, p.amount), "0.00"),
    [periods],
  );
  const [applyOpen, setApplyOpen] = useState(false);
  const invoices = useInvoicePages(20);
  const rows = useMemo<InvoiceOut[]>(
    () => (invoices.data?.pages ?? []).flatMap((p) => p.items),
    [invoices.data],
  );

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Card size="small">
        <Space style={{ width: "100%", justifyContent: "space-between" }} align="start" wrap>
          <Statistic
            title={t("billing.invoiceEligibleTotal")}
            value={moneyOr(formatMoney(total), eligibleQ.data != null)}
          />
          <Button
            type="primary"
            disabled={periods.length === 0}
            onClick={() => setApplyOpen(true)}
          >
            {t("billing.invoiceApply")}
          </Button>
        </Space>
        {eligibleQ.isError ? (
          // 加载失败绝不能伪装成「无可开票账期」
          <DataErrorAlert onRetry={() => void eligibleQ.refetch()} />
        ) : periods.length > 0 ? (
          <Space wrap size={8} style={{ marginTop: 12 }}>
            {periods.map((p) => (
              <Tag key={p.period}>{`${p.period} · ${formatMoney(p.amount)}`}</Tag>
            ))}
          </Space>
        ) : (
          !eligibleQ.isLoading && (
            <Typography.Text type="secondary" style={{ display: "block", marginTop: 12 }}>
              {t("billing.invoiceNoEligible")}
            </Typography.Text>
          )
        )}
        <Typography.Text type="secondary" style={{ display: "block", marginTop: 8, fontSize: 12 }}>
          {t("billing.invoiceEligibleHint")}
          {" · "}
          {t("billing.invoiceManualNote")}
        </Typography.Text>
      </Card>
      <Table
        rowKey="id"
        size="small"
        pagination={false}
        scroll={{ x: 860 }}
        loading={invoices.isLoading}
        dataSource={rows}
        locale={{
          emptyText: invoices.isError ? (
            <TableErrorEmpty onRetry={() => void invoices.refetch()} />
          ) : (
            t("billing.invoiceNone")
          ),
        }}
        columns={[
          { title: t("billing.colPeriod"), dataIndex: "period" },
          {
            title: t("billing.colAmount"),
            render: (_, r) => <span>{formatMoney(r.amount)}</span>,
          },
          { title: t("billing.colTitle"), dataIndex: "title", ellipsis: true },
          {
            title: t("billing.colStatus"),
            render: (_, r) => {
              const m = metaOf(invoiceStatusMap, r.status);
              return <Tag color={m?.color}>{m ? t(m.labelKey) : r.status}</Tag>;
            },
          },
          {
            title: t("billing.colInvoiceNo"),
            render: (_, r) => r.invoice_no ?? "—",
          },
          {
            title: t("billing.colRejectReason"),
            render: (_, r) => (r.status === "rejected" ? (r.reject_reason ?? "—") : "—"),
          },
          { title: t("billing.colTime"), dataIndex: "created_at", render: formatDateTime },
        ]}
      />
      {invoices.hasNextPage && (
        <Button
          block
          loading={invoices.isFetchingNextPage}
          onClick={() => void invoices.fetchNextPage()}
        >
          {t("billing.loadMore")}
        </Button>
      )}
      <InvoiceApplyModal periods={periods} open={applyOpen} onClose={() => setApplyOpen(false)} />
    </Space>
  );
}

function BillingPage() {
  const { formatMoney } = useFormat();
  const { t, i18n } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { tab, month: monthParam } = Route.useSearch();
  const [rechargeOpen, setRechargeOpen] = useState(false);
  const [warnHours, setWarnHours] = useState<number>();
  const activeTab: BillingTab = tab ?? "bills";
  const [exporting, setExporting] = useState(false);
  const walletQ = useWallet({ refetchInterval: 10_000 });
  const { data: wallet } = walletQ;
  const { data: me } = useMe();
  const { data: policies } = usePolicies();
  // 本地时区取当月(toISOString 是 UTC 切片,+08:00 月初凌晨会切到上个月)
  const now = new Date();
  const currentMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  // 月份选择器:月汇总/环图/小时账单/CSV 导出共用同一口径
  const month = monthParam ?? currentMonth;
  const { date, tzOffsetMinutes } = localToday();
  // 三个查询必须共用同一个本地时区口径,否则 31 天日账单之和 ≠ 月账单
  const summaryQ = useBillSummary(month, tzOffsetMinutes);
  const { data: summary } = summaryQ;
  const dailyQ = useDailySummary(date, tzOffsetMinutes);
  const { data: daily } = dailyQ;
  const setThreshold = useSetWarnThreshold({ onSuccess: () => message.success(t("billing.thresholdSaved")) });

  const saveThreshold = (v: number | undefined) => {
    if (v != null) setThreshold.mutate(v); // 范围校验在 InputNumber min/max 与后端 ge/le
  };

  const setSearch = (patch: { tab?: BillingTab; month?: string }) =>
    void navigate({
      to: "/billing",
      search: (prev: { tab?: string; month?: string }) => ({
        tab: patch.tab ?? (prev.tab as BillingTab | undefined),
        month: patch.month ?? prev.month,
      }),
      replace: true,
    });

  // 服务端流式导出:小时账单跟随所选月份;触顶标记行 → 截断提示
  const exportCsv = async () => {
    setExporting(true);
    try {
      const lang = i18n.resolvedLanguage === "en-US" ? ("en-US" as const) : ("zh-CN" as const);
      const isBills = activeTab === "bills";
      const csv = (await exportBillingApiV1BillingExportGet(
        isBills
          ? { dataset: "hourly", month, tz_offset_minutes: tzOffsetMinutes, lang }
          : { dataset: "ledger", tz_offset_minutes: tzOffsetMinutes, lang },
      )) as string;
      downloadCsv(isBills ? `superdl-hourly-${month}.csv` : "superdl-ledger.csv", csv);
      if (csv.includes(TRUNCATED_MARKER)) {
        message.warning(t("billing.csvTruncated"));
      } else {
        message.success(t("billing.csvExported"));
      }
    } catch {
      message.error(t("billing.csvExportFailed"));
    } finally {
      setExporting(false);
    }
  };

  const pieData = (summary?.items ?? []).map((i) => ({
    name: i.instance_name ?? t("billing.instanceRef", { id: i.instance_id }),
    value: parseFloat(i.total_amount),
  }));

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("billing.title")}
      </Typography.Title>
      {walletQ.isError && <DataErrorAlert onRetry={() => void walletQ.refetch()} />}
      {(summaryQ.isError || dailyQ.isError) && (
        <DataErrorAlert
          onRetry={() => {
            void summaryQ.refetch();
            void dailyQ.refetch();
          }}
        />
      )}
      {/* /me 未就绪(加载/失败)时不弹实名横幅:已实名用户绝不能被误判成未认证 */}
      {policies?.real_name_required_for_recharge && me != null && me.verification_status !== "verified" && (
        <Alert
          type="warning"
          showIcon
          title={t("billing.realNameRequired")}
          action={
            <Link to="/settings">
              <Button size="small">{t("billing.goVerify")}</Button>
            </Link>
          }
        />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card>
            <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
              <Statistic
                title={t("billing.availableBalance")}
                value={moneyOr(formatMoney(wallet?.balance), wallet != null)}
                styles={{ content: { fontSize: 32 } }}
              />
              <Button type="primary" size="large" onClick={() => setRechargeOpen(true)}>
                {t("billing.recharge")}
              </Button>
            </Space>
            <Space style={{ marginTop: 12 }}>
              <Typography.Text type="secondary">{t("settings.warnThresholdLabel")}</Typography.Text>
              <InputNumber
                size="small"
                min={1}
                max={168}
                aria-label={t("settings.warnThresholdLabel")}
                value={warnHours ?? me?.low_balance_warn_hours}
                onChange={(v) => setWarnHours(v ?? undefined)}
                onPressEnter={() => saveThreshold(warnHours ?? me?.low_balance_warn_hours)}
              />
              <Button
                size="small"
                loading={setThreshold.isPending}
                onClick={() => saveThreshold(warnHours ?? me?.low_balance_warn_hours)}
              >
                {t("billing.save")}
              </Button>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t("settings.warnThresholdHint")}
              </Typography.Text>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card
            title={t("billing.monthSpend", { month })}
            extra={
              <DatePicker
                picker="month"
                size="small"
                allowClear={false}
                aria-label={t("billing.monthPicker")}
                value={dayjs(`${month}-01`)}
                disabledDate={(d) => d.isAfter(dayjs(), "month")}
                onChange={(d) => {
                  if (d) setSearch({ month: d.format("YYYY-MM") });
                }}
              />
            }
          >
            <Row>
              <Col xs={24} md={10}>
                <Statistic
                  title={t("billing.gpuTotal")}
                  value={moneyOr(formatMoney(summary?.gpu_total), summary != null)}
                />
                <Statistic
                  title={t("billing.diskTotal")}
                  value={moneyOr(formatMoney(summary?.disk_total), summary != null)}
                  styles={{ content: { fontSize: 16 } }}
                />
                <Statistic
                  title={t("instances.labelToday")}
                  value={moneyOr(
                    formatMoney(daily ? addAmounts(daily.gpu_total, daily.disk_total) : null),
                    daily != null,
                  )}
                  styles={{ content: { fontSize: 16 } }}
                />
              </Col>
              <Col xs={24} md={14}>
                {pieData.length ? (
                  <EChart
                    style={{ height: 160 }}
                    option={{
                      tooltip: { trigger: "item" },
                      series: [
                        {
                          type: "pie",
                          radius: ["45%", "70%"],
                          data: pieData,
                          label: { fontSize: 11 },
                        },
                      ],
                    }}
                  />
                ) : (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("billing.noSpendThisMonth")} />
                )}
              </Col>
            </Row>
          </Card>
        </Col>
      </Row>

      <Card>
        <Tabs
          activeKey={activeTab}
          onChange={(k) => setSearch({ tab: k as BillingTab })}
          tabBarExtraContent={
            // CSV 导出仅覆盖账单/流水两个口径;退款/发票 Tab 不导出
            activeTab === "bills" || activeTab === "ledger" ? (
              <Button size="small" loading={exporting} onClick={() => void exportCsv()}>
                {t("billing.exportCsv")}
              </Button>
            ) : undefined
          }
          items={[
            {
              key: "bills",
              label: t("billing.tabBills"),
              children: <HourlyBillsTable month={month} tzOffsetMinutes={tzOffsetMinutes} />,
            },
            { key: "ledger", label: t("billing.tabLedger"), children: <LedgerTable /> },
            { key: "refunds", label: t("billing.tabRefunds"), children: <RefundTab /> },
            { key: "invoices", label: t("billing.tabInvoices"), children: <InvoiceTab /> },
          ]}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t("copy.dailyCostNote")}
          {" · "}
          {t("copy.billingDayBoundary")}
        </Typography.Text>
      </Card>
      <RechargeModal open={rechargeOpen} onClose={() => setRechargeOpen(false)} />
    </Space>
  );
}
