import { adminColors, formatDateTime, formatMoney, statusColors } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  App,
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Popconfirm,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import ReactECharts from "echarts-for-react";

import {
  type AlertRow,
  type DeadTaskRow,
  type NodeRow,
  type OversellRow,
  type TenantRow,
  isApiError,
  useAdminInstances,
  useAlerts,
  useDeadTasks,
  useDiscardDeadTask,
  useNodes,
  useOversellReport,
  useRetryDeadTask,
  useRevenueReport,
  useTenants,
} from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/")({
  component: Overview,
});

function OversellChart({ rows }: { rows: OversellRow[] }) {
  const pools = rows.map((r) => r.pool);
  const option = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#94A3B8" } },
    grid: { left: 48, right: 48, top: 40, bottom: 28 },
    xAxis: { type: "category", data: pools, axisLabel: { color: "#94A3B8" } },
    yAxis: [
      {
        type: "value",
        name: "超卖率",
        axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%`, color: "#94A3B8" },
        splitLine: { lineStyle: { color: "#1E293B" } },
      },
      {
        type: "value",
        name: "利用率 %",
        max: 100,
        axisLabel: { color: "#94A3B8" },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "实际超卖率",
        type: "bar",
        data: rows.map((r) => r.oversell_ratio),
        itemStyle: { color: adminColors.dataAccent },
        barWidth: 36,
      },
      {
        name: "真实利用率(24h)",
        type: "line",
        yAxisIndex: 1,
        data: rows.map((r) => r.util_avg_24h),
        itemStyle: { color: adminColors.alertAccent },
        markLine: {
          symbol: "none",
          lineStyle: { type: "dashed" },
          data: [
            { yAxis: 60, label: { formatter: "上调阈值 60%", color: "#94A3B8" } },
            { yAxis: 85, label: { formatter: "回调阈值 85%", color: "#94A3B8" } },
          ],
        },
      },
    ],
  };
  return <ReactECharts option={option} style={{ height: 320 }} theme={undefined} />;
}

function PoolOccupancy({ nodes }: { nodes: NodeRow[] }) {
  const pools = [...new Set(nodes.map((n) => n.pool_label))];
  const used = pools.map((p) =>
    nodes.filter((n) => n.pool_label === p).reduce((s, n) => s + n.gpu_used, 0),
  );
  const free = pools.map(
    (p, i) =>
      nodes.filter((n) => n.pool_label === p).reduce((s, n) => s + n.gpu_total, 0) - used[i]!,
  );
  const option = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#94A3B8" } },
    grid: { left: 80, right: 24, top: 32, bottom: 28 },
    xAxis: { type: "value", axisLabel: { color: "#94A3B8" }, splitLine: { lineStyle: { color: "#1E293B" } } },
    yAxis: { type: "category", data: pools, axisLabel: { color: "#94A3B8" } },
    series: [
      { name: "已租", type: "bar", stack: "t", data: used, itemStyle: { color: statusColors.green } },
      { name: "空闲", type: "bar", stack: "t", data: free, itemStyle: { color: "#334155" } },
    ],
  };
  return <ReactECharts option={option} style={{ height: 220 }} />;
}

/** 值班首屏第二排:任务死信(重放交还幂等 handler;忽略需原因)。 */
function DeadTasksCard() {
  const { message } = App.useApp();
  const qc = useQueryClient();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const { data, queryKey } = useDeadTasks();
  const rows: DeadTaskRow[] = data ?? [];
  const retry = useRetryDeadTask();
  const discard = useDiscardDeadTask();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  if (rows.length === 0) return null;
  return (
    <Col span={24}>
    <Card
      title={
        <Space size={8}>
          任务死信
          <Tag color="red">{rows.length} 条待处理</Tag>
        </Space>
      }
    >
      <Table<DeadTaskRow>
        size="small"
        rowKey="id"
        pagination={false}
        scroll={{ x: 860 }}
        dataSource={rows}
        columns={[
          { title: "任务", dataIndex: "type", width: 150 },
          {
            title: "载荷",
            dataIndex: "payload",
            render: (v: Record<string, unknown>) => (
              <code style={{ fontSize: 12 }}>{JSON.stringify(v)}</code>
            ),
          },
          { title: "重试", dataIndex: "retries", width: 70 },
          {
            title: "最后错误",
            dataIndex: "last_error",
            render: (v: string | null) => (
              <span style={{ color: "#F87171", fontSize: 12 }}>{v ?? "-"}</span>
            ),
          },
          { title: "时间", dataIndex: "updated_at", width: 150, render: formatDateTime },
          {
            title: "操作",
            width: 170,
            render: (_, r) => (
              <Space>
                <Popconfirm
                  title="重放该任务?(handler 幂等,置回队列重新执行)"
                  disabled={!writable}
                  onConfirm={async () => {
                    try {
                      await retry.mutateAsync({ taskId: r.id });
                      message.success("已置回队列");
                      refresh();
                    } catch (e) {
                      message.error(isApiError(e) ? e.message : "重放失败");
                    }
                  }}
                >
                  <Button size="small" type="primary" disabled={!writable}>
                    重放
                  </Button>
                </Popconfirm>
                <ReasonAction
                  label="忽略"
                  title="忽略死信"
                  confirmText={`确认不再执行任务 #${r.id}(${r.type})?`}
                  danger
                  disabled={!writable}
                  disabledReason="仅运维/超管可操作"
                  onSubmit={async (reason) => {
                    await discard.mutateAsync({ taskId: r.id, data: { reason } });
                    refresh();
                  }}
                />
              </Space>
            ),
          },
        ]}
      />
    </Card>
    </Col>
  );
}

function Overview() {
  const { data: oversell } = useOversellReport();
  const { data: nodesData } = useNodes();
  const { data: instances } = useAdminInstances();
  const { data: tenants } = useTenants();
  const { data: alertsData } = useAlerts();
  const { data: revenue } = useRevenueReport();

  const oversellRows: OversellRow[] = oversell ?? [];
  const nodes: NodeRow[] = nodesData ?? [];
  const alerts: AlertRow[] = alertsData ?? [];
  const tenantRows: TenantRow[] = tenants ?? [];
  const running = (instances ?? []).filter((i) => i.status === "running").length;
  const signupDelta = revenue ? revenue.today_signups - revenue.yesterday_signups : 0;

  return (
    <Row gutter={[16, 16]}>
      <Col xs={12} xl={4}>
        <Card>
          <Statistic title="今日收入" value={revenue ? formatMoney(revenue.today_revenue) : "—"} />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            昨日 {revenue ? formatMoney(revenue.yesterday_revenue) : "—"}
          </Typography.Text>
        </Card>
      </Col>
      <Col xs={12} xl={4}>
        <Card>
          <Statistic title="本月收入" value={revenue ? formatMoney(revenue.month_revenue) : "—"} />
        </Card>
      </Col>
      <Col xs={12} xl={4}>
        <Card>
          <Statistic title="今日新注册" value={revenue ? revenue.today_signups : "—"} />
          <Typography.Text
            style={{ fontSize: 12, color: signupDelta >= 0 ? "#4ADE80" : "#F87171" }}
          >
            {signupDelta >= 0 ? "▲" : "▼"} {Math.abs(signupDelta)} 较昨日
          </Typography.Text>
        </Card>
      </Col>
      <Col xs={12} xl={4}>
        <Card><Statistic title="活跃实例" value={running} /></Card>
      </Col>
      <Col xs={12} xl={4}>
        <Card>
          <Statistic
            title="付费租户"
            value={`${tenantRows.filter((t) => Number(t.total_consumed) > 0).length} / ${tenantRows.length}`}
          />
        </Card>
      </Col>
      <Col xs={12} xl={4}>
        <Card>
          <Statistic
            title="告警(总)"
            value={alerts.length}
            valueStyle={alerts.some((a) => a.severity === "critical") ? { color: "#F87171" } : undefined}
          />
        </Card>
      </Col>

      <DeadTasksCard />

      <Col span={17}>
        <Card
          title="实际超卖率 vs 真实利用率(共享池定价的数据闭环)"
          extra={<Typography.Text type="secondary">P95&lt;60% 才上调超卖</Typography.Text>}
        >
          {oversellRows.length ? <OversellChart rows={oversellRows} /> : <Empty />}
        </Card>
        <Card title="GPU 池占用" style={{ marginTop: 16 }}>
          {nodes.length ? <PoolOccupancy nodes={nodes} /> : <Empty />}
        </Card>
      </Col>
      <Col span={7}>
        <Card title="实时告警流" styles={{ body: { maxHeight: 560, overflow: "auto" } }}>
          {alerts.length === 0 && <Empty description="暂无告警" />}
          {alerts.map((a) => (
            <div key={a.id} style={{ marginBottom: 12 }}>
              <Badge
                color={a.severity === "critical" ? "#DC2626" : adminColors.alertAccent}
                text={<b>{a.title}</b>}
              />
              <div style={{ color: "#94A3B8", fontSize: 12, paddingLeft: 14 }}>
                {formatDateTime(a.created_at)} · {a.content}
              </div>
            </div>
          ))}
        </Card>
      </Col>
    </Row>
  );
}
