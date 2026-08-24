import { adminColors, formatDateTime, statusColors } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "../../lib/format";
import EChart from "../../components/EChart";

import {
  type AlertRow,
  type DeadTaskRow,
  type OversellRow,
  type OverviewOut,
  isApiError,
  useAckAlert,
  useAlertUnreadCount,
  useAlerts,
  useDeadTasks,
  useDiscardDeadTask,
  useOversellReport,
  useOverview,
  useRetryDeadTask,
  useRevenueReport,
} from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { useApiErrorText } from "../../lib/apiError";
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

/** 告警跳转目标(后端按现有字段派生 target_kind/target_id):无 target 不可点。 */
function alertLink(a: AlertRow): { to: string; search?: { q: string } } | null {
  if (a.target_kind === "tenant" && a.target_id) {
    return { to: "/tenants", search: { q: a.target_id } };
  }
  if (a.target_kind === "node") return { to: "/nodes" };
  if (a.target_kind === "ticket") return { to: "/tickets" };
  return null;
}

/** 实时告警流:severity 过滤、确认闭环(留确认人+时间)、点击跳受影响节点/租户。 */
function AlertStreamCard() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const [severity, setSeverity] = useState<string | undefined>();
  const { data, queryKey } = useAlerts(severity ? { severity } : undefined);
  const ack = useAckAlert({
    mutation: {
      onSuccess: () => {
        message.success(t("overview.ackDone"));
        void qc.invalidateQueries({ queryKey });
        void qc.invalidateQueries({ queryKey: ["admin", "alerts", "unread-count"] });
      },
      onError: (e) => message.error(errText(e, t("overview.ackFailed"))),
    },
  });
  const alerts: AlertRow[] = data ?? [];

  return (
    <Card
      title={t("overview.alertStream")}
      extra={
        <Select
          size="small"
          allowClear
          placeholder={t("overview.severityFilter")}
          style={{ width: 120 }}
          value={severity}
          onChange={(v) => setSeverity(v)}
          options={["info", "warning", "critical"].map((s) => ({ value: s, label: s }))}
        />
      }
      styles={{ body: { maxHeight: 560, overflow: "auto" } }}
    >
      {alerts.length === 0 && <Empty description={t("shell.noAlerts")} />}
      {alerts.map((a) => {
        const link = alertLink(a);
        return (
          <div key={a.id} style={{ marginBottom: 12 }}>
            <Badge
              color={a.severity === "critical" ? adminColors.critical : adminColors.alertAccent}
              text={
                link ? (
                  <Link to={link.to} search={link.search}>
                    <b>{a.title}</b>
                  </Link>
                ) : (
                  <b>{a.title}</b>
                )
              }
            />
            <div style={{ color: adminColors.textSecondary, fontSize: 12, paddingLeft: 14 }}>
              {formatDateTime(a.created_at)} · {a.content}
            </div>
            <div style={{ paddingLeft: 14, marginTop: 2 }}>
              {a.acked_at ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {t("overview.ackedBy", {
                    name: a.acked_by_username ?? `#${a.acked_by ?? "-"}`,
                    time: formatDateTime(a.acked_at),
                  })}
                </Typography.Text>
              ) : (
                <Tooltip title={writable ? "" : t("overview.opsOnly")}>
                  <Button
                    size="small"
                    disabled={!writable}
                    loading={ack.isPending && ack.variables?.alertId === a.id}
                    onClick={() => ack.mutate({ alertId: a.id })}
                  >
                    {t("overview.ack")}
                  </Button>
                </Tooltip>
              )}
            </div>
          </div>
        );
      })}
    </Card>
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
  // 总览聚合:全部精确 COUNT(全角色可读),不从截断列表推算
  const { data: ov, isError: ovError, error: ovErr, refetch: refetchOv } = useOverview();
  const { data: alertsData } = useAlerts();
  const { data: revenue } = useRevenueReport();
  // 「告警(总)」 = 未确认告警精确计数(独立计数端点;截断的告警流长度会低估)
  const { data: unread } = useAlertUnreadCount();

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
            value={unread?.count ?? "—"}
            styles={{
              content: alerts.some((a) => a.severity === "critical" && !a.acked_at)
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
        <AlertStreamCard />
      </Col>
    </Row>
  );
}
