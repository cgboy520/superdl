/** 费用中心:余额卡/充值 Modal(mock 渠道+轮询)/消费概览环图/账单与收支明细。 */

import { type LedgerEntryOut, type RechargeOut } from "@superdl/api-client";
import {
  copy,
  formatDateTime,
  formatDuration,
  formatHourlyPrice,
  formatMoney,
  tabularNums,
} from "@superdl/ui";
import { createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Empty,
  InputNumber,
  Modal,
  Radio,
  Row,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import ReactECharts from "echarts-for-react";
import { useMemo, useState } from "react";

import { useCreateRecharge, useMockPay, useSetWarnThreshold } from "../api/mutations";
import {
  useBillSummary,
  useHourlyBills,
  useLedger,
  useMe,
  useRecharge,
  useWallet,
} from "../api/queries";
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
        <Space orientation="vertical" size={16} style={{ width: "100%" }}>
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
            addonBefore="¥"
          />
          <Typography.Text type="secondary">
            渠道:mock(开发环境);微信/支付宝待商户资质接入后开放
          </Typography.Text>
          <Button
            type="primary"
            block
            loading={create.isPending}
            onClick={() =>
              create.mutate({
                body: { amount: amount.toFixed(2), channel: "mock" },
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
          <Alert type="info" showIcon message={`订单 ${order.order_no},等待支付…`} />
          <div
            style={{
              height: 160,
              border: "1px dashed #d9d9d9",
              borderRadius: 8,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexDirection: "column",
              gap: 8,
            }}
          >
            <Typography.Text type="secondary">二维码占位</Typography.Text>
            <Typography.Text code copyable style={{ fontSize: 11 }}>
              {order.qr_url}
            </Typography.Text>
          </div>
          <Button
            block
            loading={mockPay.isPending}
            onClick={() =>
              mockPay.mutate({ order_no: order.order_no, amount: order.amount })
            }
          >
            模拟支付成功(开发环境)
          </Button>
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
              <span style={{ ...tabularNums, color: r.amount.startsWith("-") ? undefined : "#16A34A" }}>
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
  const { data: wallet } = useWallet({ refetchInterval: 10_000 });
  const { data: me } = useMe();
  const month = new Date().toISOString().slice(0, 7);
  const { data: summary } = useBillSummary(month);
  const { data: bills } = useHourlyBills({ limit: 50 });
  const setThreshold = useSetWarnThreshold({ onSuccess: () => message.success("阈值已更新") });

  const pieData = (summary?.items ?? []).map((i) => ({
    name: `实例 #${i.instance_id}`,
    value: parseFloat(i.total_amount),
  }));

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        费用中心
      </Typography.Title>
      <Row gutter={16}>
        <Col span={12}>
          <Card>
            <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
              <Statistic
                title="可用余额"
                value={formatMoney(wallet?.balance)}
                valueStyle={{ fontSize: 32, ...tabularNums }}
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
                defaultValue={me?.low_balance_warn_hours}
                onPressEnter={(e) =>
                  setThreshold.mutate(Number((e.target as HTMLInputElement).value))
                }
              />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                预计可用时长低于该值时短信+站内信提醒(回车保存)
              </Typography.Text>
            </Space>
          </Card>
        </Col>
        <Col span={12}>
          <Card title={`本月消费(${month})`}>
            <Row>
              <Col span={10}>
                <Statistic
                  title="GPU 时费"
                  value={formatMoney(summary?.gpu_total)}
                  valueStyle={tabularNums}
                />
                <Statistic
                  title="日常费用(数据盘)"
                  value={formatMoney(summary?.disk_total)}
                  valueStyle={{ fontSize: 16, ...tabularNums }}
                />
              </Col>
              <Col span={14}>
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
          items={[
            {
              key: "bills",
              label: "小时账单",
              children: (
                <Table
                  rowKey="id"
                  size="small"
                  pagination={false}
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
