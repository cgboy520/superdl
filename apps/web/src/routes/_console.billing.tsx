/**
 * 费用中心:余额卡(阈值带保存钮)/充值 Modal(渠道 Tab 预留+真二维码+有效期)/
 * 消费概览(月+今日)环图/账单与收支明细(客户端 CSV 导出)。
 */

import {
  listHourlyBillsApiV1BillsHourlyGet,
  getLedgerApiV1WalletLedgerGet,
  type BillHourlyOut,
  type LedgerEntryOut,
  type RechargeOut,
} from "@superdl/api-client";
import { addAmounts, formatDateTime, localToday, statusColors } from "@superdl/ui";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Empty,
  InputNumber,
  Modal,
  QRCode,
  Radio,
  Row,
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
import { useEffect, useMemo, useState } from "react";

import { useCreateRecharge, useMockPay, useSetWarnThreshold } from "../api/mutations";
import { DataErrorAlert, moneyOr } from "../components/QueryState";
import {
  useBillSummary,
  useDailySummary,
  useHourlyBills,
  useLedgerPages,
  useMe,
  usePolicies,
  useRecharge,
  useSiteConfig,
  useWallet,
} from "../api/queries";
import { downloadCsv, toCsv } from "../lib/csv";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/billing")({
  beforeLoad: requireAuth,
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
  const { formatMoney } = useFormat();
  const { t } = useTranslation();
  const { message } = App.useApp();
  // 金额必须按字符串走(InputNumber stringMode),禁止经二进制浮点
  const [amount, setAmount] = useState("100.00");
  const [order, setOrder] = useState<RechargeOut | null>(null);
  const [idem, setIdem] = useState(() => crypto.randomUUID());
  const [pickedChannel, setPickedChannel] = useState<string | null>(null);

  // 渠道开关来自管理端·平台配置(site-config 公开端点)
  const { data: site } = useSiteConfig();
  const enabled = {
    wechat: site?.payment_channels.wechat ?? false,
    alipay: site?.payment_channels.alipay ?? false,
    mock: site?.payment_channels.mock ?? false,
  };
  const firstEnabled = enabled.wechat ? "wechat" : enabled.alipay ? "alipay" : "mock";
  const channel = pickedChannel ?? firstEnabled;
  const anyEnabled = enabled.wechat || enabled.alipay || enabled.mock;

  const create = useCreateRecharge({
    onSuccess: (d) => setOrder(d as RechargeOut),
  });
  const mockPay = useMockPay({ onSuccess: () => message.success(t("billing.mockPaySent")) });
  const { data: polled } = useRecharge(order?.order_no ?? "", {
    enabled: Boolean(order),
    // 到终态(paid/closed/failed)即停,不再空转打接口
    refetchInterval: (q) =>
      q.state.data && q.state.data.status !== "pending" ? false : 2_000,
  });

  const status = polled?.status ?? order?.status;
  const paid = status === "paid";

  const reset = () => {
    setOrder(null);
    setIdem(crypto.randomUUID());
    onClose();
  };

  return (
    <Modal title={t("billing.recharge")} open={open} onCancel={reset} footer={null}>
      {!order ? (
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
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
                  <Tooltip title={t("copy.channelComingSoon")}>{t("billing.wechat")}</Tooltip>
                ),
                disabled: !enabled.wechat,
              },
              {
                key: "alipay",
                label: enabled.alipay ? (
                  t("billing.alipay")
                ) : (
                  <Tooltip title={t("copy.channelComingSoon")}>{t("billing.alipay")}</Tooltip>
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
            value={PRESET_AMOUNTS.find((v) => Number(v) === Number(amount))}
            onChange={(e) => setAmount(e.target.value as string)}
            options={PRESET_AMOUNTS.map((v) => ({ value: v, label: `¥${Number(v)}` }))}
          />
          <InputNumber
            style={{ width: 200 }}
            min="1"
            max="50000"
            precision={2}
            stringMode
            value={amount}
            onChange={(v) => setAmount(v ?? "0")}
            prefix="¥"
          />
          <Button
            type="primary"
            block
            disabled={!anyEnabled}
            loading={create.isPending}
            onClick={() =>
              create.mutate({
                body: { amount, channel },
                idempotencyKey: idem,
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
          <Typography.Text>{t("billing.credited", { amount: formatMoney(order.amount) })}</Typography.Text>
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
              no: order.order_no,
              time: formatDateTime(polled?.expires_at ?? order.expires_at),
            })}
            description={
              <PayCountdown expiresAt={polled?.expires_at ?? order.expires_at} />
            }
          />
          <div style={{ display: "flex", justifyContent: "center" }}>
            <QRCode value={order.qr_url ?? order.order_no} size={168} />
          </div>
          {order.channel === "wechat" && (
            <Typography.Text type="secondary" style={{ display: "block", textAlign: "center" }}>
              {t("billing.scanWithWechat")}
            </Typography.Text>
          )}
          {order.channel === "alipay" && (
            <Typography.Text type="secondary" style={{ display: "block", textAlign: "center" }}>
              {t("billing.scanWithAlipay")}
            </Typography.Text>
          )}
          {order.channel === "mock" && (
            <Button
              block
              loading={mockPay.isPending}
              onClick={() =>
                mockPay.mutate({ order_no: order.order_no, amount: order.amount })
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

function LedgerTable() {
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const { data, isFetchingNextPage, hasNextPage, fetchNextPage } = useLedgerPages(20);
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
        dataSource={merged}
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

function BillingPage() {
  const { formatDuration, formatHourlyPrice, formatMoney } = useFormat();
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [rechargeOpen, setRechargeOpen] = useState(false);
  const [warnHours, setWarnHours] = useState<number>();
  const [activeTab, setActiveTab] = useState<"bills" | "ledger">("bills");
  const [exporting, setExporting] = useState(false);
  const walletQ = useWallet({ refetchInterval: 10_000 });
  const { data: wallet } = walletQ;
  const { data: me } = useMe();
  const { data: policies } = usePolicies();
  // 本地时区取当月(toISOString 是 UTC 切片,+08:00 月初凌晨会切到上个月)
  const now = new Date();
  const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const { date, tzOffsetMinutes } = localToday();
  // 三个查询必须共用同一个本地时区口径,否则 31 天日账单之和 ≠ 月账单
  const { data: summary } = useBillSummary(month, tzOffsetMinutes);
  const { data: daily } = useDailySummary(date, tzOffsetMinutes);
  // 账单表与 CSV 导出必须同口径(都按当月)
  const { data: bills } = useHourlyBills({ month, tz_offset_minutes: tzOffsetMinutes, limit: 50 });
  const setThreshold = useSetWarnThreshold({ onSuccess: () => message.success(t("billing.thresholdSaved")) });

  const saveThreshold = (v: number | undefined) => {
    if (v != null) setThreshold.mutate(v); // 范围校验在 InputNumber min/max 与后端 ge/le
  };

  const exportCsv = async () => {
    setExporting(true);
    try {
      if (activeTab === "bills") {
        const rows: BillHourlyOut[] = [];
        let cursor: string | undefined;
        for (let i = 0; i < 200; i++) {
          const page = await listHourlyBillsApiV1BillsHourlyGet({
            month,
            tz_offset_minutes: tzOffsetMinutes,
            limit: 100,
            cursor,
          });
          rows.push(...page.items);
          if (!page.next_cursor) break;
          cursor = page.next_cursor;
        }
        downloadCsv(
          `superdl-hourly-${month}.csv`,
          toCsv(
            [t("instances.colBillHour"), t("billing.csvInstanceId"), t("billing.csvSeconds"), t("billing.csvUnitPrice"), t("billing.csvGpuCount"), t("billing.csvAmount")],
            rows.map((r) => [
              formatDateTime(r.hour_start),
              r.instance_id,
              r.seconds_used,
              r.unit_price,
              r.gpu_count,
              r.amount,
            ]),
          ),
        );
      } else {
        const rows: LedgerEntryOut[] = [];
        let cursor: string | undefined;
        for (let i = 0; i < 200; i++) {
          const page = await getLedgerApiV1WalletLedgerGet({ limit: 100, cursor });
          rows.push(...page.items);
          if (!page.next_cursor) break;
          cursor = page.next_cursor;
        }
        downloadCsv(
          "superdl-ledger.csv",
          toCsv(
            [t("billing.colTime"), t("billing.colType"), t("billing.csvAmount"), t("billing.csvBalanceAfter"), t("billing.csvRef"), t("billing.colRemark")],
            rows.map((r) => [
              formatDateTime(r.created_at),
              ((): string => { const m = LEDGER_TYPE[r.type as LedgerTypeKey] as (typeof LEDGER_TYPE)[LedgerTypeKey] | undefined; return m ? t(m.labelKey) : r.type; })(),
              r.amount,
              r.balance_after,
              r.ref_type ? `${r.ref_type}:${r.ref_id ?? ""}` : "",
              r.remark ?? "",
            ]),
          ),
        );
      }
      message.success(t("billing.csvExported"));
    } catch {
      message.error(t("billing.csvExportFailed"));
    } finally {
      setExporting(false);
    }
  };

  const pieData = (summary?.items ?? []).map((i) => ({
    name: t("billing.instanceRef", { id: i.instance_id }),
    value: parseFloat(i.total_amount),
  }));

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("billing.title")}
      </Typography.Title>
      {walletQ.isError && <DataErrorAlert onRetry={() => void walletQ.refetch()} />}
      {policies?.real_name_required_for_recharge && me?.verification_status !== "verified" && (
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
          <Card title={t("billing.monthSpend", { month })}>
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
          onChange={(k) => setActiveTab(k as "bills" | "ledger")}
          tabBarExtraContent={
            <Button size="small" loading={exporting} onClick={() => void exportCsv()}>
              {t("billing.exportCsv")}
            </Button>
          }
          items={[
            {
              key: "bills",
              label: t("billing.tabBills"),
              children: (
                <Table
                  rowKey="id"
                  size="small"
                  pagination={false}
                  scroll={{ x: 760 }}
                  dataSource={bills?.items ?? []}
                  columns={[
                    { title: t("instances.colBillHour"), render: (_, r) => formatDateTime(r.hour_start) },
                    { title: t("billing.colInstance"), render: (_, r) => `#${r.instance_id}` },
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
                      render: (_, r) => (
                        <span>{formatMoney(r.amount)}</span>
                      ),
                    },
                  ]}
                />
              ),
            },
            { key: "ledger", label: t("billing.tabLedger"), children: <LedgerTable /> },
          ]}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t("copy.dailyCostNote")}
        </Typography.Text>
      </Card>
      <RechargeModal open={rechargeOpen} onClose={() => setRechargeOpen(false)} />
    </Space>
  );
}
