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
import {
  addAmounts,
  copy,
  formatCountdown,
  formatDateTime,
  formatDuration,
  formatHourlyPrice,
  formatMoney,
  localToday,
  statusColors,
  tabularNums,
} from "@superdl/ui";
import { createFileRoute, Link } from "@tanstack/react-router";
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
import ReactECharts from "echarts-for-react";
import { useMemo, useState } from "react";

import { useCreateRecharge, useMockPay, useSetWarnThreshold } from "../api/mutations";
import { DataErrorAlert, moneyOr } from "../components/QueryState";
import {
  useBillSummary,
  useDailySummary,
  useHourlyBills,
  useLedger,
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

const LEDGER_TYPE: Record<string, { label: string; color: string }> = {
  recharge: { label: "充值", color: "green" },
  consume: { label: "消费", color: "blue" },
  refund: { label: "退款", color: "orange" },
  adjust: { label: "调账", color: "purple" },
};

function RechargeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = App.useApp();
  const [amount, setAmount] = useState<number>(100);
  const [order, setOrder] = useState<RechargeOut | null>(null);
  const [idem, setIdem] = useState(() => crypto.randomUUID());
  const [pickedChannel, setPickedChannel] = useState<string | null>(null);

  // 渠道开关来自管理端·平台配置(site-config 公开端点),商户接入后即时开放
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
  const mockPay = useMockPay({ onSuccess: () => message.success("模拟支付已发送") });
  const { data: polled } = useRecharge(order?.order_no ?? "", {
    enabled: Boolean(order),
    refetchInterval: 2_000,
  });

  const status = polled?.status ?? order?.status;
  const paid = status === "paid";

  const reset = () => {
    setOrder(null);
    setIdem(crypto.randomUUID());
    onClose();
  };

  return (
    <Modal title="充值" open={open} onCancel={reset} footer={null}>
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
                  "微信支付"
                ) : (
                  <Tooltip title={copy.channelComingSoon}>微信支付</Tooltip>
                ),
                disabled: !enabled.wechat,
              },
              {
                key: "alipay",
                label: enabled.alipay ? (
                  "支付宝"
                ) : (
                  <Tooltip title={copy.channelComingSoon}>支付宝</Tooltip>
                ),
                disabled: !enabled.alipay,
              },
              ...(enabled.mock
                ? [{ key: "mock", label: "模拟支付(开发环境)" }]
                : []),
            ]}
          />
          <Radio.Group
            optionType="button"
            value={[50, 100, 500].includes(amount) ? amount : undefined}
            onChange={(e) => setAmount(e.target.value as number)}
            options={[50, 100, 500].map((v) => ({ value: v, label: `¥${v}` }))}
          />
          <InputNumber
            style={{ width: 200 }}
            min={1}
            max={50000}
            precision={2}
            value={amount}
            onChange={(v) => setAmount(v ?? 0)}
            prefix="¥"
          />
          <Button
            type="primary"
            block
            disabled={!anyEnabled}
            loading={create.isPending}
            onClick={() =>
              create.mutate({
                body: { amount: amount.toFixed(2), channel },
                idempotencyKey: idem,
              })
            }
          >
            生成支付二维码
          </Button>
        </Space>
      ) : paid ? (
        <Space orientation="vertical" align="center" style={{ width: "100%" }}>
          <Typography.Title level={4} type="success">
            支付成功
          </Typography.Title>
          <Typography.Text>已到账 {formatMoney(order.amount)}</Typography.Text>
          <Button type="primary" onClick={reset}>
            完成
          </Button>
        </Space>
      ) : (
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            title={`订单 ${order.order_no} · 有效期${formatCountdown(
              polled?.expires_at ?? order.expires_at,
            ).replace("剩 ", " ")},等待支付…`}
          />
          <div style={{ display: "flex", justifyContent: "center" }}>
            <QRCode value={order.qr_url ?? order.order_no} size={168} />
          </div>
          {order.channel === "wechat" && (
            <Typography.Text type="secondary" style={{ display: "block", textAlign: "center" }}>
              请使用微信「扫一扫」完成支付
            </Typography.Text>
          )}
          {order.channel === "alipay" && (
            <Typography.Text type="secondary" style={{ display: "block", textAlign: "center" }}>
              请使用支付宝「扫一扫」完成支付
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
              模拟支付成功(开发环境)
            </Button>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            支付完成后本窗口每 2 秒自动确认到账;超时未支付订单 2 小时后自动关闭
          </Typography.Text>
        </Space>
      )}
    </Modal>
  );
}

function LedgerTable() {
  const [cursor, setCursor] = useState<string>();
  const [rows, setRows] = useState<LedgerEntryOut[]>([]);
  const { data, isFetching } = useLedger({ cursor, limit: 20 });
  const merged = useMemo(() => {
    const seen = new Set<number>();
    const out: LedgerEntryOut[] = [];
    for (const r of [...rows, ...(data?.items ?? [])]) {
      if (!seen.has(r.id)) {
        seen.add(r.id);
        out.push(r);
      }
    }
    return out;
  }, [rows, data]);

  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <Table
        rowKey="id"
        size="small"
        pagination={false}
        scroll={{ x: 760 }}
        dataSource={merged}
        columns={[
          { title: "时间", render: (_, r) => formatDateTime(r.created_at) },
          {
            title: "类型",
            render: (_, r) => {
              const meta = LEDGER_TYPE[r.type] ?? { label: r.type, color: "default" };
              return <Tag color={meta.color}>{meta.label}</Tag>;
            },
          },
          {
            title: "金额",
            render: (_, r) => (
              <span
                style={{ ...tabularNums, color: r.amount.startsWith("-") ? undefined : statusColors.green }}
              >
                {r.amount.startsWith("-") ? "" : "+"}
                {formatMoney(r.amount)}
              </span>
            ),
          },
          {
            title: "余额快照",
            render: (_, r) => <span style={tabularNums}>{formatMoney(r.balance_after)}</span>,
          },
          { title: "备注", dataIndex: "remark" },
        ]}
      />
      {data?.next_cursor && (
        <Button
          block
          loading={isFetching}
          onClick={() => {
            setRows(merged);
            setCursor(data.next_cursor ?? undefined);
          }}
        >
          加载更多
        </Button>
      )}
    </Space>
  );
}

function BillingPage() {
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
  const { data: summary } = useBillSummary(month);
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes);
  const { data: bills } = useHourlyBills({ limit: 50 });
  const setThreshold = useSetWarnThreshold({ onSuccess: () => message.success("阈值已更新") });

  const saveThreshold = (v: number | undefined) => {
    if (v != null && v >= 1 && v <= 168) setThreshold.mutate(v);
  };

  const exportCsv = async () => {
    setExporting(true);
    try {
      if (activeTab === "bills") {
        const rows: BillHourlyOut[] = [];
        let cursor: string | undefined;
        for (let i = 0; i < 200; i++) {
          const page = await listHourlyBillsApiV1BillsHourlyGet({ month, limit: 100, cursor });
          rows.push(...page.items);
          if (!page.next_cursor) break;
          cursor = page.next_cursor;
        }
        downloadCsv(
          `superdl-hourly-${month}.csv`,
          toCsv(
            ["计费小时", "实例ID", "运行秒数", "单价(元/时)", "卡数", "金额(元)"],
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
            ["时间", "类型", "金额(元)", "余额快照(元)", "关联", "备注"],
            rows.map((r) => [
              formatDateTime(r.created_at),
              LEDGER_TYPE[r.type]?.label ?? r.type,
              r.amount,
              r.balance_after,
              r.ref_type ? `${r.ref_type}:${r.ref_id ?? ""}` : "",
              r.remark ?? "",
            ]),
          ),
        );
      }
      message.success("已导出 CSV");
    } catch {
      message.error("导出失败,请稍后重试");
    } finally {
      setExporting(false);
    }
  };

  const pieData = (summary?.items ?? []).map((i) => ({
    name: `实例 #${i.instance_id}`,
    value: parseFloat(i.total_amount),
  }));

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        费用中心
      </Typography.Title>
      {walletQ.isError && <DataErrorAlert onRetry={() => void walletQ.refetch()} />}
      {policies?.real_name_required_for_recharge && me?.verification_status !== "verified" && (
        <Alert
          type="warning"
          showIcon
          title="按监管要求,完成实名认证后方可充值"
          action={
            <Link to="/settings">
              <Button size="small">去认证</Button>
            </Link>
          }
        />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card>
            <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
              <Statistic
                title="可用余额"
                value={moneyOr(wallet?.balance, wallet != null)}
                styles={{ content: { fontSize: 32, ...tabularNums } }}
              />
              <Button type="primary" size="large" onClick={() => setRechargeOpen(true)}>
                充值
              </Button>
            </Space>
            <Space style={{ marginTop: 12 }}>
              <Typography.Text type="secondary">低余额预警阈值(小时)</Typography.Text>
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
                保存
              </Button>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                预计可用时长低于该值时短信+站内信提醒
              </Typography.Text>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title={`本月消费(${month})`}>
            <Row>
              <Col xs={24} md={10}>
                <Statistic
                  title="GPU 时费"
                  value={formatMoney(summary?.gpu_total)}
                  styles={{ content: tabularNums }}
                />
                <Statistic
                  title="日常费用(数据盘)"
                  value={formatMoney(summary?.disk_total)}
                  styles={{ content: { fontSize: 16, ...tabularNums } }}
                />
                <Statistic
                  title="今日消费"
                  value={formatMoney(daily ? addAmounts(daily.gpu_total, daily.disk_total) : null)}
                  styles={{ content: { fontSize: 16, ...tabularNums } }}
                />
              </Col>
              <Col xs={24} md={14}>
                {pieData.length ? (
                  <ReactECharts
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
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本月暂无消费" />
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
              导出 CSV
            </Button>
          }
          items={[
            {
              key: "bills",
              label: "小时账单",
              children: (
                <Table
                  rowKey="id"
                  size="small"
                  pagination={false}
                  scroll={{ x: 760 }}
                  dataSource={bills?.items ?? []}
                  columns={[
                    { title: "计费小时", render: (_, r) => formatDateTime(r.hour_start) },
                    { title: "实例", render: (_, r) => `#${r.instance_id}` },
                    { title: "时长", render: (_, r) => formatDuration(r.seconds_used) },
                    {
                      title: "单价",
                      render: (_, r) => (
                        <span style={tabularNums}>
                          {formatHourlyPrice(r.unit_price)} × {r.gpu_count}
                        </span>
                      ),
                    },
                    {
                      title: "金额",
                      render: (_, r) => (
                        <span style={tabularNums}>{formatMoney(r.amount)}</span>
                      ),
                    },
                  ]}
                />
              ),
            },
            { key: "ledger", label: "收支明细", children: <LedgerTable /> },
          ]}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {copy.dailyCostNote}
        </Typography.Text>
      </Card>
      <RechargeModal open={rechargeOpen} onClose={() => setRechargeOpen(false)} />
    </Space>
  );
}
