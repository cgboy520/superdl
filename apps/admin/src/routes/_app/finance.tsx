import { formatDateTime, formatMoney } from "@superdl/ui";
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
import { AuditTable } from "../../components/AuditTable";
import { canWriteFinance, useAdminRole, useAuth } from "../../stores/auth";

export const Route = createFileRoute("/_app/finance")({
  component: FinancePage,
});

function ReconciliationCard() {
  const [day, setDay] = useState<Dayjs>(dayjs());
  const { data: report } = useReconciliation(day.format("YYYY-MM-DD"));
  const diffHigh = (report?.diff_pct ?? 0) > 2;

  return (
    <Card
      title="日对账 · 事件计费 vs 指标估算"
      extra={<DatePicker value={day} onChange={(d) => d && setDay(d)} allowClear={false} />}
    >
      <Row gutter={16}>
        <Col span={6}>
          <Statistic title="事件计费合计(计费主依据)" value={formatMoney(report?.billed_total)} />
        </Col>
        <Col span={6}>
          <Statistic title="指标估算合计(对账参考)" value={formatMoney(report?.estimated_total)} />
        </Col>
        <Col span={6}>
          <Statistic
            title="diff%"
            value={report?.diff_pct ?? 0}
            suffix="%"
            valueStyle={diffHigh ? { color: "#F87171" } : { color: "#4ADE80" }}
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
            { title: "实例 ID", dataIndex: "instance_id" },
            { title: "事件计费", dataIndex: "billed", render: (v: string) => formatMoney(v) },
            { title: "指标估算", dataIndex: "estimated", render: (v: string) => formatMoney(v) },
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
  const [status, setStatus] = useState<string | undefined>();
  const { data } = useOrders(status ? { status } : undefined);
  const orders: OrderRow[] = data ?? [];
  return (
    <>
      <Select
        allowClear
        placeholder="状态过滤"
        style={{ width: 160, marginBottom: 12 }}
        value={status}
        onChange={setStatus}
        options={["pending", "paid", "closed", "failed"].map((v) => ({ value: v, label: v }))}
      />
      <Table<OrderRow>
        scroll={{ x: 900 }}
        rowKey="order_no"
        dataSource={orders}
        columns={[
          { title: "订单号", dataIndex: "order_no" },
          { title: "租户", dataIndex: "user_id", width: 80 },
          { title: "金额", dataIndex: "amount", render: (v: string) => formatMoney(v) },
          { title: "渠道", dataIndex: "channel" },
          {
            title: "状态",
            dataIndex: "status",
            render: (v: string) => (
              <Tag color={{ paid: "green", pending: "blue", closed: "default", failed: "red" }[v]}>
                {v}
              </Tag>
            ),
          },
          { title: "创建时间", dataIndex: "created_at", render: formatDateTime },
        ]}
      />
    </>
  );
}

function AdjustmentsTab() {
  const { message } = App.useApp();
  const role = useAdminRole();
  const { admin } = useAuth();
  const writable = canWriteFinance(role);
  const qc = useQueryClient();
  const { data, queryKey } = useAdjustments();
  const rows: AdjustmentRow[] = data ?? [];
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<{ user_id: number; amount: number; reason: string }>();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  const create = useCreateAdjustment({
    mutation: {
      onSuccess: () => {
        message.success("调账单已发起,等待第二位管理员复核");
        setCreating(false);
        form.resetFields();
        refresh();
      },
      onError: (e) => message.error(isApiError(e) ? e.message : "发起失败"),
    },
  });
  const review = useReviewAdjustment({
    mutation: {
      onSuccess: () => {
        message.success("复核完成");
        refresh();
      },
      onError: (e) =>
        message.error(
          isApiError(e) && e.code === "ADMIN_SECOND_REVIEW_REQUIRED"
            ? "调账必须由第二位管理员复核(不能自审)"
            : isApiError(e)
              ? e.message
              : "复核失败",
        ),
    },
  });

  return (
    <>
      <Tooltip title={writable ? "" : "仅财务/超管可发起调账"}>
        <Button
          type="primary"
          disabled={!writable}
          style={{ marginBottom: 12 }}
          onClick={() => setCreating(true)}
        >
          发起调账
        </Button>
      </Tooltip>
      <Table<AdjustmentRow>
        scroll={{ x: 1000 }}
        rowKey="id"
        dataSource={rows}
        columns={[
          { title: "单号", dataIndex: "id", width: 70 },
          { title: "租户", dataIndex: "user_id", width: 80 },
          {
            title: "金额",
            dataIndex: "amount",
            render: (v: string) => (
              <span style={{ color: v.startsWith("-") ? "#F87171" : "#4ADE80" }}>
                {formatMoney(v)}
              </span>
            ),
          },
          { title: "原因", dataIndex: "reason" },
          {
            title: "状态",
            dataIndex: "status",
            render: (v: string) => (
              <Tag color={{ pending: "blue", approved: "green", rejected: "red" }[v]}>
                {{ pending: "待复核", approved: "已生效", rejected: "已驳回" }[v] ?? v}
              </Tag>
            ),
          },
          { title: "发起人", dataIndex: "created_by", width: 80 },
          {
            title: "复核",
            render: (_, r) => {
              if (r.status !== "pending") {
                return (
                  <span style={{ color: "#64748B" }}>
                    {r.reviewed_by ? `#${r.reviewed_by} ${r.review_comment ?? ""}` : "-"}
                  </span>
                );
              }
              const isCreator = admin?.id === r.created_by;
              return (
                <Tooltip
                  title={
                    !writable
                      ? "仅财务/超管可复核"
                      : isCreator
                        ? "发起人不能复核自己的调账单(双人复核)"
                        : ""
                  }
                >
                  <Space>
                    <Popconfirm
                      title={`确认通过并生效 ${formatMoney(r.amount)} 调账?`}
                      onConfirm={() =>
                        review.mutate({ adjustmentId: r.id, data: { approve: true } })
                      }
                      disabled={!writable || isCreator}
                    >
                      <Button size="small" type="primary" disabled={!writable || isCreator}>
                        通过
                      </Button>
                    </Popconfirm>
                    <Popconfirm
                      title="确认驳回?"
                      onConfirm={() =>
                        review.mutate({
                          adjustmentId: r.id,
                          data: { approve: false, comment: "复核驳回" },
                        })
                      }
                      disabled={!writable || isCreator}
                    >
                      <Button size="small" danger disabled={!writable || isCreator}>
                        驳回
                      </Button>
                    </Popconfirm>
                  </Space>
                </Tooltip>
              );
            },
          },
          { title: "发起时间", dataIndex: "created_at", render: formatDateTime },
        ]}
      />
      <Modal
        title="发起调账(需第二位管理员复核后生效)"
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={async () => {
          const values = await form.validateFields();
          create.mutate({
            data: {
              user_id: values.user_id,
              amount: values.amount.toFixed(2),
              reason: values.reason,
            },
          });
        }}
        okButtonProps={{ loading: create.isPending }}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="user_id" label="租户 ID" rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="amount"
            label="金额(正=补偿入账,负=扣减)"
            rules={[{ required: true }]}
          >
            <InputNumber step={0.01} style={{ width: "100%" }} placeholder="如 25.50 或 -10.00" />
          </Form.Item>
          <Form.Item
            name="reason"
            label="原因(必填,入审计)"
            rules={[{ required: true, min: 2 }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

const ANOMALY_META: Record<AnomalyRow["kind"], { label: string; color: string }> = {
  lost_callback: { label: "回调丢失", color: "orange" },
  closed_order: { label: "超时关单", color: "default" },
  negative_balance: { label: "负余额", color: "red" },
};

function AnomaliesTab() {
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
        title: `渠道核验 · ${orderNo}`,
        content: (
          <Space orientation="vertical" size={4}>
            <span>订单状态:{r.order_status} · 订单金额 {formatMoney(r.order_amount)}</span>
            <span>渠道状态:{r.channel_status}</span>
            <span>渠道金额:{r.channel_amount ? formatMoney(r.channel_amount) : "—"}</span>
            <span>渠道单号:{r.channel_txn_id ?? "—"}</span>
            <b style={{ color: r.matches ? "#4ADE80" : "#F87171" }}>
              {r.matches ? "✓ 渠道已支付且金额一致,可补单" : "✗ 渠道未支付或金额不符,不可补单"}
            </b>
          </Space>
        ),
      });
    } catch (e) {
      message.error(isApiError(e) ? e.message : "核验失败");
    }
  };

  return (
    <>
      <Table<AnomalyRow>
        scroll={{ x: 960 }}
        rowKey={(r) => `${r.kind}:${r.order_no ?? r.user_id}`}
        loading={isLoading}
        dataSource={rows}
        locale={{ emptyText: "当前无异常,查单 poller 每 2 分钟自动收敛丢回调" }}
        columns={[
          {
            title: "类型",
            dataIndex: "kind",
            width: 110,
            render: (v: AnomalyRow["kind"]) => (
              <Tag color={ANOMALY_META[v].color}>{ANOMALY_META[v].label}</Tag>
            ),
          },
          {
            title: "订单 / 主体",
            render: (_, r) => (
              <>
                {r.order_no ?? `租户 ${r.user_id}`}
                <div style={{ color: "#94A3B8", fontSize: 12 }}>{r.detail}</div>
              </>
            ),
          },
          { title: "金额", dataIndex: "amount", width: 110, render: (v: string) => formatMoney(v) },
          { title: "发现于", dataIndex: "created_at", width: 150, render: formatDateTime },
          {
            title: "动作",
            width: 200,
            render: (_, r) => {
              if (r.kind === "negative_balance") {
                return <span style={{ color: "#94A3B8" }}>可在「调账」发起核销</span>;
              }
              return (
                <Space>
                  <Button size="small" onClick={() => void doVerify(r.order_no!)}>
                    查渠道单
                  </Button>
                  <Tooltip title={writable ? "" : "仅财务/超管可补单"}>
                    <Button
                      size="small"
                      type="primary"
                      disabled={!writable}
                      onClick={() => setBackfillTarget(r)}
                    >
                      补单
                    </Button>
                  </Tooltip>
                </Space>
              );
            },
          },
        ]}
      />
      <Modal
        title={`手动补单 · ${backfillTarget?.order_no ?? ""}`}
        open={Boolean(backfillTarget)}
        onCancel={() => setBackfillTarget(null)}
        okText="核验并入账"
        okButtonProps={{ loading: backfill.isPending }}
        onOk={async () => {
          const { reason } = await reasonForm.validateFields();
          try {
            await backfill.mutateAsync({
              orderNo: backfillTarget!.order_no!,
              data: { reason },
            });
            message.success("补单成功,已入账");
            setBackfillTarget(null);
            reasonForm.resetFields();
            refresh();
          } catch (e) {
            message.error(isApiError(e) ? e.message : "补单失败");
          }
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <span style={{ color: "#94A3B8" }}>
            提交时服务端将实时向渠道核验:仅当渠道侧已支付且金额与订单一致才会入账。
          </span>
          <Form form={reasonForm} layout="vertical">
            <Form.Item
              name="reason"
              label="原因(必填,入审计)"
              rules={[{ required: true, min: 2, message: "请填写补单原因(至少 2 字)" }]}
            >
              <Input.TextArea rows={2} placeholder="如:回调丢失,查单确认后补入账" />
            </Form.Item>
          </Form>
        </Space>
      </Modal>
    </>
  );
}

function FinancePage() {
  const { data: anomalies } = useAnomalies();
  const anomalyCount = anomalies?.length ?? 0;
  return (
    <>
      <ReconciliationCard />
      <Card style={{ marginTop: 16 }}>
        <Tabs
          items={[
            { key: "orders", label: "充值流水", children: <OrdersTab /> },
            { key: "adjustments", label: "调账(双人复核)", children: <AdjustmentsTab /> },
            {
              key: "anomalies",
              label: (
                <Space size={6}>
                  异常清单
                  {anomalyCount > 0 && <Tag color="red">{anomalyCount}</Tag>}
                </Space>
              ),
              children: <AnomaliesTab />,
            },
            { key: "audit", label: "审计日志", children: <AuditTable /> },
          ]}
        />
      </Card>
    </>
  );
}
