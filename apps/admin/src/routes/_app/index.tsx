import { adminColors, formatDateTime, statusColors } from "@superdl/ui";
import { createFileRoute } from "@tanstack/react-router";
import { Badge, Card, Col, Empty, Row, Statistic, Typography } from "antd";
import ReactECharts from "echarts-for-react";

import {
  type AlertRow,
  type NodeRow,
  type OversellRow,
  type TenantRow,
  useAdminInstances,
  useAlerts,
  useNodes,
  useOversellReport,
  useTenants,
} from "../../api";

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

function Overview() {
  const { data: oversell } = useOversellReport();
  const { data: nodesData } = useNodes();
  const { data: instances } = useAdminInstances();
  const { data: tenants } = useTenants();
  const { data: alertsData } = useAlerts();

  const oversellRows: OversellRow[] = oversell ?? [];
  const nodes: NodeRow[] = nodesData ?? [];
  const alerts: AlertRow[] = alertsData ?? [];
  const tenantRows: TenantRow[] = tenants ?? [];
  const running = (instances ?? []).filter((i) => i.status === "running").length;

  return (
    <Row gutter={[16, 16]}>
      <Col span={6}>
        <Card><Statistic title="活跃实例" value={running} /></Card>
      </Col>
      <Col span={6}>
        <Card><Statistic title="租户数" value={tenantRows.length} /></Card>
      </Col>
      <Col span={6}>
        <Card>
          <Statistic
            title="付费租户"
            value={tenantRows.filter((t) => Number(t.total_consumed) > 0).length}
          />
        </Card>
      </Col>
      <Col span={6}>
        <Card>
          <Statistic
            title="告警(总)"
            value={alerts.length}
            valueStyle={alerts.some((a) => a.severity === "critical") ? { color: "#F87171" } : undefined}
          />
        </Card>
      </Col>

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
