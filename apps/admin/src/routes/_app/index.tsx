import { adminColors, formatDateTime, statusColors } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";

import { useFormat } from "../../lib/format";
import EChart from "../../components/EChart";

import {
  type AlertRow,
  type DeadTaskRow,
  type OversellRow,
  type OverviewOut,
  isApiError,
  useAlerts,
  useDeadTasks,
  useDiscardDeadTask,
  useOversellReport,
  useOverview,
  useRetryDeadTask,
  useRevenueReport,
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

function PoolOccupancy({ pools }: { pools: OverviewOut["pools"] }) {
  const { t } = useTranslation();
  const names = pools.map((p) => p.pool);
  const used = pools.map((p) => p.gpu_used);
  // 空闲只算 Ready 节点的卡;非 Ready 节点的物理卡画成第三段,不再混进「空闲」
  const free = pools.map((p) => Math.max(0, p.ready_gpu_total - p.gpu_used));
  const notReady = pools.map((p) => Math.max(0, p.gpu_total - p.ready_gpu_total));
  const option = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: adminColors.textSecondary } },
    grid: { left: 80, right: 24, top: 32, bottom: 28 },
    xAxis: { type: "value", axisLabel: { color: adminColors.textSecondary }, splitLine: { lineStyle: { color: adminColors.gridLine } } },
    yAxis: { type: "category", data: names, axisLabel: { color: adminColors.textSecondary } },
    series: [
      { name: t("overview.rented"), type: "bar", stack: "t", data: used, itemStyle: { color: statusColors.green } },
      { name: t("overview.idle"), type: "bar", stack: "t", data: free, itemStyle: { color: adminColors.chartNeutral } },
      { name: t("overview.notReady"), type: "bar", stack: "t", data: notReady, itemStyle: { color: adminColors.alertAccent } },
    ],
  };
  return <EChart option={option} style={{ height: 220 }} />;
}

/** 值班首屏第二排:任务死信(重放/忽略都需原因 + 二次确认,handler 幂等)。 */
function DeadTasksCard() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  // 读死信需 ops/readonly:finance 看不到这张卡,也不发会 403 的轮询
  const canRead = canWriteOps(role) || role === "readonly";
  const { data, queryKey } = useDeadTasks({ enabled: canRead });
  const rows: DeadTaskRow[] = data ?? [];
  const retry = useRetryDeadTask();
  const discard = useDiscardDeadTask();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  if (!canRead || rows.length === 0) return null;
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
            // 原始堆栈可能上千字符,必须一行截断 + 悬浮看全文,否则会撑开整张表
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
                <ReasonAction
                  label={t("overview.replay")}
                  title={t("overview.replayTitle")}
                  confirmText={t("overview.replayConfirmMeta", { id: r.id, type: r.type })}
                  disabled={!writable}
                  disabledReason={t("overview.opsOnly")}
                  onSubmit={async (reason) => {
                    await retry.mutateAsync({ taskId: r.id, data: { reason } });
                    refresh();
                    return t("overview.requeued");
                  }}
                />
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

/** 取数失败要显式说:渲染成「暂无数据」等于把集群不可达伪装成没数据。403 单独提示无权限。 */
function LoadFailed({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { t } = useTranslation();
  const forbidden = isApiError(error) && error.status === 403;
  return (
    <Alert
      type={forbidden ? "info" : "error"}
      showIcon
      title={forbidden ? t("overview.noPermissionData") : t("overview.loadFailed")}
      action={
        forbidden ? undefined : (
          <Button size="small" onClick={onRetry}>
            {t("overview.retry")}
          </Button>
        )
      }
    />
  );
}

function Overview() {
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const { data: oversell, isError: oversellError, error: oversellErr, refetch: refetchOversell } = useOversellReport();
  // 总览聚合:全部精确 COUNT(全角色可读),不再从截断列表推算;finance 角色也不会再 403
  const { data: ov, isError: ovError, error: ovErr, refetch: refetchOv } = useOverview();
  const { data: alertsData } = useAlerts();
  const { data: revenue } = useRevenueReport();

  const oversellRows: OversellRow[] = oversell ?? [];
  const alerts: AlertRow[] = alertsData ?? [];
  const byStatus = ov?.instances_by_status ?? {};
  const activeInstances =
    (byStatus.creating ?? 0) + (byStatus.starting ?? 0) + (byStatus.running ?? 0);
  const signupDelta = revenue ? revenue.today_signups - revenue.yesterday_signups : 0;

  return (
    <Row gutter={[16, 16]}>
      <Col xs={12} md={8} xl={6}>
        <Card>
          <Statistic title={t("overview.todayRevenue")} value={revenue ? formatMoney(revenue.today_revenue) : "—"} />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("overview.yesterdayPrefix", { amount: revenue ? formatMoney(revenue.yesterday_revenue) : "—" })}
          </Typography.Text>
        </Card>
      </Col>
      <Col xs={12} md={8} xl={6}>
        <Card>
          <Statistic title={t("overview.monthRevenue")} value={revenue ? formatMoney(revenue.month_revenue) : "—"} />
        </Card>
      </Col>
      <Col xs={12} md={8} xl={6}>
        <Card>
          <Statistic title={t("overview.todaySignups")} value={revenue ? revenue.today_signups : "—"} />
          <Typography.Text
            style={{ fontSize: 12, color: signupDelta >= 0 ? adminColors.positive : adminColors.negative }}
          >
            {signupDelta >= 0 ? "▲" : "▼"} {t("overview.vsYesterday", { count: Math.abs(signupDelta) })}
          </Typography.Text>
        </Card>
      </Col>
      <Col xs={12} md={8} xl={6}>
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
      <Col xs={12} md={8} xl={6}>
        <Card>
          <Statistic title={t("overview.activeInstances")} value={ov ? activeInstances : "—"} />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("overview.instanceStatusHint", {
              stopped: byStatus.stopped ?? 0,
              failed: byStatus.failed ?? 0,
            })}
          </Typography.Text>
        </Card>
      </Col>
      <Col xs={12} md={8} xl={6}>
        <Card>
          <Statistic
            title={t("overview.payingTenants")}
            value={ov ? `${ov.paying_tenants} / ${ov.tenants_total}` : "—"}
          />
        </Card>
      </Col>
      <Col xs={12} md={8} xl={6}>
        <Card>
          <Statistic
            title={t("overview.nodesHealth")}
            value={ov ? `${ov.nodes_ready} / ${ov.nodes_total}` : "—"}
          />
          <Typography.Text
            style={{
              fontSize: 12,
              color: ov && ov.nodes_missing > 0 ? adminColors.negative : adminColors.textSecondary,
            }}
          >
            {t("overview.nodesMissing", { count: ov?.nodes_missing ?? 0 })}
          </Typography.Text>
        </Card>
      </Col>

      <DeadTasksCard />

      <Col xs={24} xl={16}>
        <Card
          title={t("overview.oversellChartTitle")}
          extra={<Typography.Text type="secondary">{t("overview.oversellHint")}</Typography.Text>}
        >
          {oversellError ? (
            <LoadFailed error={oversellErr} onRetry={() => void refetchOversell()} />
          ) : oversellRows.length ? (
            <OversellChart rows={oversellRows} />
          ) : (
            <Empty />
          )}
        </Card>
        <Card title={t("overview.poolOccupancy")} style={{ marginTop: 16 }}>
          {ovError ? (
            <LoadFailed error={ovErr} onRetry={() => void refetchOv()} />
          ) : ov && ov.pools.length ? (
            <PoolOccupancy pools={ov.pools} />
          ) : (
            <Empty />
          )}
        </Card>
      </Col>
      <Col xs={24} xl={8}>
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
