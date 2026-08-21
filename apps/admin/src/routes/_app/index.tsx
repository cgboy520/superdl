import { adminColors, formatDateTime, statusColors } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Popconfirm,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";

import { useApiErrorText } from "../../lib/apiError";
import { useFormat } from "../../lib/format";
import EChart from "../../components/EChart";

import {
  type AlertRow,
  type DeadTaskRow,
  type NodeRow,
  type OversellRow,
  type TenantRow,
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
  const { t } = useTranslation();
  const pools = rows.map((r) => r.pool);
  const option = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: adminColors.textSecondary } },
    grid: { left: 48, right: 48, top: 40, bottom: 28 },
    xAxis: { type: "category", data: pools, axisLabel: { color: adminColors.textSecondary } },
    yAxis: [
      {
        type: "value",
        name: t("overview.axisOversell"),
        axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%`, color: adminColors.textSecondary },
        splitLine: { lineStyle: { color: adminColors.gridLine } },
      },
      {
        type: "value",
        name: t("overview.axisUtil"),
        max: 100,
        axisLabel: { color: adminColors.textSecondary },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: t("overview.seriesOversell"),
        type: "bar",
        data: rows.map((r) => r.oversell_ratio),
        itemStyle: { color: adminColors.dataAccent },
        barWidth: 36,
      },
      {
        name: t("overview.seriesUtil"),
        type: "line",
        yAxisIndex: 1,
        data: rows.map((r) => r.util_avg_24h),
        itemStyle: { color: adminColors.alertAccent },
        markLine: {
          symbol: "none",
          lineStyle: { type: "dashed" },
          data: [
            { yAxis: 60, label: { formatter: t("overview.raiseThreshold"), color: adminColors.textSecondary } },
            { yAxis: 85, label: { formatter: t("overview.lowerThreshold"), color: adminColors.textSecondary } },
          ],
        },
      },
    ],
  };
  return <EChart option={option} style={{ height: 320 }} theme={undefined} />;
}

function PoolOccupancy({ nodes }: { nodes: NodeRow[] }) {
  const { t } = useTranslation();
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
    legend: { textStyle: { color: adminColors.textSecondary } },
    grid: { left: 80, right: 24, top: 32, bottom: 28 },
    xAxis: { type: "value", axisLabel: { color: adminColors.textSecondary }, splitLine: { lineStyle: { color: adminColors.gridLine } } },
    yAxis: { type: "category", data: pools, axisLabel: { color: adminColors.textSecondary } },
    series: [
      { name: t("overview.rented"), type: "bar", stack: "t", data: used, itemStyle: { color: statusColors.green } },
      { name: t("overview.idle"), type: "bar", stack: "t", data: free, itemStyle: { color: adminColors.chartNeutral } },
    ],
  };
  return <EChart option={option} style={{ height: 220 }} />;
}

/** 值班首屏第二排:任务死信(重放交还幂等 handler;忽略需原因)。 */
function DeadTasksCard() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
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
  // 默认折叠:死信原始堆栈每条约 270px,展开摆在首屏会把镇店图表(超卖率 vs 利用率)
  // 挤到第二屏。一行摘要 + 展开详情,既不漏报也不占 C 位。
  return (
    <Col span={24}>
    <Collapse
      items={[
        {
          key: "dead",
          label: (
            <Space size={8}>
              <Badge status="error" />
              <b>{t("overview.deadTasks")}</b>
              <Tag color="red">{t("overview.pendingCount", { count: rows.length })}</Tag>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t("overview.deadTasksSummary", {
                  types: [...new Set(rows.map((r) => r.type))].join("、"),
                })}
              </Typography.Text>
            </Space>
          ),
          children: (
      <Table<DeadTaskRow>
        size="small"
        rowKey="id"
        pagination={false}
        scroll={{ x: 860 }}
        dataSource={rows}
        columns={[
          { title: t("overview.colTask"), dataIndex: "type", width: 150 },
          {
            title: t("overview.colPayload"),
            dataIndex: "payload",
            render: (v: Record<string, unknown>) => (
              <code style={{ fontSize: 12 }}>{JSON.stringify(v)}</code>
            ),
          },
          { title: t("overview.colRetries"), dataIndex: "retries", width: 70 },
          {
            title: t("overview.colLastError"),
            dataIndex: "last_error",
            // 原始堆栈可能上千字符:一行截断 + 悬浮看全文,不让它把整张表撑开
            render: (v: string | null) => (
              <Tooltip title={<span style={{ whiteSpace: "pre-wrap" }}>{v ?? "-"}</span>}>
                <span
                  style={{
                    color: adminColors.negative,
                    fontSize: 12,
                    display: "block",
                    maxWidth: 360,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {v ?? "-"}
                </span>
              </Tooltip>
            ),
          },
          { title: t("overview.colTime"), dataIndex: "updated_at", width: 150, render: formatDateTime },
          {
            title: t("overview.colActions"),
            width: 170,
            render: (_, r) => (
              <Space>
                <Popconfirm
                  title={t("overview.replayConfirm")}
                  disabled={!writable}
                  onConfirm={async () => {
                    try {
                      await retry.mutateAsync({ taskId: r.id });
                      message.success(t("overview.requeued"));
                      refresh();
                    } catch (e) {
                      message.error(errText(e, t("overview.replayFailed")));
                    }
                  }}
                >
                  <Button size="small" type="primary" disabled={!writable}>
                    {t("overview.replay")}
                  </Button>
                </Popconfirm>
                <ReasonAction
                  label={t("overview.ignore")}
                  title={t("overview.ignoreTitle")}
                  confirmText={t("overview.ignoreConfirm", { id: r.id, type: r.type })}
                  danger
                  disabled={!writable}
                  disabledReason={t("overview.opsOnly")}
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
          ),
        },
      ]}
    />
    </Col>
  );
}

/** 取数失败要显式说:渲染成「暂无数据」等于把集群不可达伪装成没数据。 */
function LoadFailed({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <Alert
      type="error"
      showIcon
      message={t("overview.loadFailed")}
      action={
        <Button size="small" onClick={onRetry}>
          {t("overview.retry")}
        </Button>
      }
    />
  );
}

function Overview() {
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const { data: oversell, isError: oversellError, refetch: refetchOversell } = useOversellReport();
  const { data: nodesData, isError: nodesError, refetch: refetchNodes } = useNodes();
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
          <Statistic title={t("overview.todayRevenue")} value={revenue ? formatMoney(revenue.today_revenue) : "—"} />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("overview.yesterdayPrefix", { amount: revenue ? formatMoney(revenue.yesterday_revenue) : "—" })}
          </Typography.Text>
        </Card>
      </Col>
      <Col xs={12} xl={4}>
        <Card>
          <Statistic title={t("overview.monthRevenue")} value={revenue ? formatMoney(revenue.month_revenue) : "—"} />
        </Card>
      </Col>
      <Col xs={12} xl={4}>
        <Card>
          <Statistic title={t("overview.todaySignups")} value={revenue ? revenue.today_signups : "—"} />
          <Typography.Text
            style={{ fontSize: 12, color: signupDelta >= 0 ? adminColors.positive : adminColors.negative }}
          >
            {signupDelta >= 0 ? "▲" : "▼"} {t("overview.vsYesterday", { count: Math.abs(signupDelta) })}
          </Typography.Text>
        </Card>
      </Col>
      <Col xs={12} xl={4}>
        <Card><Statistic title={t("overview.activeInstances")} value={running} /></Card>
      </Col>
      <Col xs={12} xl={4}>
        <Card>
          <Statistic
            title={t("overview.payingTenants")}
            value={`${tenantRows.filter((t) => Number(t.total_consumed) > 0).length} / ${tenantRows.length}`}
          />
        </Card>
      </Col>
      <Col xs={12} xl={4}>
        <Card>
          <Statistic
            title={t("overview.alertsTotal")}
            value={alerts.length}
            styles={{
              content: alerts.some((a) => a.severity === "critical")
                ? { color: adminColors.negative }
                : undefined,
            }}
          />
        </Card>
      </Col>

      <DeadTasksCard />

      <Col span={17}>
        <Card
          title={t("overview.oversellChartTitle")}
          extra={<Typography.Text type="secondary">{t("overview.oversellHint")}</Typography.Text>}
        >
          {oversellError ? (
            <LoadFailed onRetry={() => void refetchOversell()} />
          ) : oversellRows.length ? (
            <OversellChart rows={oversellRows} />
          ) : (
            <Empty />
          )}
        </Card>
        <Card title={t("overview.poolOccupancy")} style={{ marginTop: 16 }}>
          {nodesError ? (
            <LoadFailed onRetry={() => void refetchNodes()} />
          ) : nodes.length ? (
            <PoolOccupancy nodes={nodes} />
          ) : (
            <Empty />
          )}
        </Card>
      </Col>
      <Col span={7}>
        <Card title={t("overview.alertStream")} styles={{ body: { maxHeight: 560, overflow: "auto" } }}>
          {alerts.length === 0 && <Empty description={t("shell.noAlerts")} />}
          {alerts.map((a) => (
            <div key={a.id} style={{ marginBottom: 12 }}>
              <Badge
                color={a.severity === "critical" ? adminColors.critical : adminColors.alertAccent}
                text={<b>{a.title}</b>}
              />
              <div style={{ color: adminColors.textSecondary, fontSize: 12, paddingLeft: 14 }}>
                {formatDateTime(a.created_at)} · {a.content}
              </div>
            </div>
          ))}
        </Card>
      </Col>
    </Row>
  );
}
