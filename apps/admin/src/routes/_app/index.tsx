/** Operations overview: pending items, metric cards, resource charts, dead-letter tasks and the alert stream. */

import { QuestionCircleOutlined } from "@ant-design/icons";
import {
  adminColors,
  flattenPages,
  fontSize,
  formatDateTime,
  iconSize,
  layout,
  POLL,
  SEVERITY_ORDER,
  severityMap,
  space,
  statusColors,
  type AlertSeverity,
  useAutoRefresh,
  useChartTheme,
  useFormat,
} from "@superdl/ui";
import {
  DataErrorAlert,
  EChart,
  EmptyState,
  GatedButton,
  KpiGrid,
  PageContainer,
  RowActions,
  RowMoreMenu,
  StatCard,
  StatusTag,
  TableErrorEmpty,
  TriageBar,
  type TriageItem,
} from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { App, Badge, Card, Col, Collapse, Row, Select, Skeleton, Space, Table, Tag, Tooltip, Typography } from "antd";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import {
  adminKeys,
  type AlertRow,
  type DeadTaskRow,
  type OversellRow,
  type OverviewOut,
  isApiError,
  useAlertUnreadCount,
  useAlerts,
  useDeadTasks,
  useDeletionRequests,
  useDiscardDeadTask,
  useInvoices,
  useOversellReport,
  useOverview,
  useRefunds,
  useRetryDeadTask,
  useRevenueReport,
  useSettlementGaps,
} from "../../api";
import { BulkBar, runBulk } from "../../components/BulkBar";
import { ReasonAction } from "../../components/ReasonAction";
import { alertLink, useAckAlertWithFeedback } from "../../lib/alertLink";
import { canReadInvoices, canWriteFinance, canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/")({
  validateSearch: (search: Record<string, unknown>): { severity?: AlertSeverity } => ({
    severity:
      typeof search.severity === "string" && (SEVERITY_ORDER as readonly string[]).includes(search.severity)
        ? (search.severity as AlertSeverity)
        : undefined,
  }),
  component: Overview,
});

const percent = (v: number) => `${(v * 100).toFixed(0)}%`;

/** Actual oversell ratio (by pool): single-axis bar chart. */
function OversellRatioChart({ rows }: { rows: OversellRow[] }) {
  const { t } = useTranslation();
  const chartTheme = useChartTheme();
  const option = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis", valueFormatter: (v: unknown) => (typeof v === "number" ? percent(v) : "") },
    grid: { left: 56, right: 24, top: 32, bottom: 28 },
    xAxis: { type: "category", data: rows.map((r) => r.pool), axisLabel: { color: adminColors.textSecondary } },
    yAxis: {
      type: "value",
      name: t("overview.axisOversell"),
      axisLabel: { formatter: percent, color: adminColors.textSecondary },
      splitLine: { lineStyle: { color: adminColors.gridLine } },
    },
    series: [
      {
        name: t("overview.seriesOversell"),
        type: "bar",
        data: rows.map((r) => r.oversell_ratio),
        itemStyle: { color: adminColors.dataAccent },
        barWidth: 36,
      },
    ],
  };
  return (
    <EChart option={option} style={{ height: 240 }} theme={chartTheme} ariaLabel={t("overview.oversellChartTitle")} />
  );
}

/** Real utilisation 24 h (by pool): 0–100 line + raise / lower threshold dashes. */
function UtilChart({ rows }: { rows: OversellRow[] }) {
  const { t } = useTranslation();
  const chartTheme = useChartTheme();
  const option = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    grid: { left: 56, right: 24, top: 32, bottom: 28 },
    xAxis: { type: "category", data: rows.map((r) => r.pool), axisLabel: { color: adminColors.textSecondary } },
    yAxis: {
      type: "value",
      name: t("overview.axisUtil"),
      min: 0,
      max: 100,
      axisLabel: { color: adminColors.textSecondary },
      splitLine: { lineStyle: { color: adminColors.gridLine } },
    },
    series: [
      {
        name: t("overview.seriesUtil"),
        type: "line",
        data: rows.map((r) => r.util_avg_24h),
        itemStyle: { color: adminColors.alertAccent },
        markLine: {
          symbol: "none",
          lineStyle: { type: "dashed" },
          label: { position: "insideEndTop", color: adminColors.textSecondary },
          data: [
            { yAxis: 60, label: { formatter: t("overview.raiseThreshold") } },
            { yAxis: 85, label: { formatter: t("overview.lowerThreshold") } },
          ],
        },
      },
    ],
  };
  return <EChart option={option} style={{ height: 240 }} theme={chartTheme} ariaLabel={t("overview.utilChartTitle")} />;
}

function PoolOccupancy({ pools }: { pools: OverviewOut["pools"] }) {
  const { t } = useTranslation();
  const chartTheme = useChartTheme();
  const names = pools.map((p) => p.pool);
  const spotUsed = pools.map((p) => p.gpu_spot_used);
  const usedOther = pools.map((p) => p.gpu_used - p.gpu_spot_used);
  const free = pools.map((p) => Math.max(0, p.ready_gpu_total - p.gpu_used));
  const notReady = pools.map((p) => Math.max(0, p.gpu_total - p.ready_gpu_total));
  const usedTotal = pools.reduce((n, p) => n + p.gpu_used, 0);
  const spotTotal = pools.reduce((n, p) => n + p.gpu_spot_used, 0);
  const option = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle: { color: adminColors.textSecondary } },
    grid: { left: 80, right: 24, top: 44, bottom: 28 },
    xAxis: {
      type: "value",
      axisLabel: { color: adminColors.textSecondary },
      splitLine: { lineStyle: { color: adminColors.gridLine } },
    },
    yAxis: { type: "category", data: names, axisLabel: { color: adminColors.textSecondary } },
    series: [
      {
        name: t("overview.rented"),
        type: "bar",
        stack: "t",
        data: usedOther,
        itemStyle: { color: statusColors.green },
      },
      {
        name: t("overview.rentedSpot"),
        type: "bar",
        stack: "t",
        data: spotUsed,
        itemStyle: { color: statusColors.orange },
      },
      { name: t("overview.idle"), type: "bar", stack: "t", data: free, itemStyle: { color: adminColors.chartNeutral } },
      {
        name: t("overview.notReady"),
        type: "bar",
        stack: "t",
        data: notReady,
        itemStyle: { color: adminColors.alertAccent },
      },
    ],
  };
  return (
    <>
      <EChart option={option} style={{ height: 220 }} theme={chartTheme} ariaLabel={t("overview.poolOccupancy")} />
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {t("overview.spotReclaimable", { used: usedTotal, spot: spotTotal })}
      </Typography.Text>
    </>
  );
}

/** Dead-letter task card (replay/ignore need a reason + second confirmation); expanded by default when dead letters exist, #dead-tasks anchors the pending bar. */
function DeadTasksCard() {
  const { t, i18n } = useTranslation(["admin", "shared"]);
  const qc = useQueryClient();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const canRead = canWriteOps(role) || role === "readonly";
  const { data, queryKey, isLoading, isError, error, refetch } = useDeadTasks({ enabled: canRead });
  const rows: DeadTaskRow[] = data ?? [];
  const retry = useRetryDeadTask();
  const discard = useDiscardDeadTask();
  const refresh = () => void qc.invalidateQueries({ queryKey });
  const [collapsed, setCollapsed] = useState<boolean | null>(null);
  const expanded = collapsed === null ? rows.length > 0 : !collapsed;
  const [selected, setSelected] = useState<number[]>([]);
  const { message } = App.useApp();
  const bulk = async (kind: "retry" | "discard", reason: string) => {
    const { ok, failed } = await runBulk(selected, (id) =>
      kind === "retry"
        ? retry.mutateAsync({ taskId: id, data: { reason } })
        : discard.mutateAsync({ taskId: id, data: { reason } }),
    );
    setSelected([]);
    refresh();
    if (failed > 0) message.warning(t("bulk.partial", { ok, failed }));
    return t("bulk.done", { count: ok });
  };

  if (!canRead) return null;
  if (!isError && !isLoading && rows.length === 0) return null;
  return (
    <div id="dead-tasks" style={{ marginTop: 16, scrollMarginTop: layout.scrollMarginTop }}>
      <Collapse
        activeKey={expanded ? ["dead"] : []}
        onChange={(keys) => setCollapsed(keys.length === 0)}
        items={[
          {
            key: "dead",
            label: (
              <Space size={space.sm}>
                <Badge status="error" />
                <b>{t("overview.deadTasks")}</b>
                {isError ? (
                  <Tag color="orange">{t("common.loadFailed", { ns: "shared" })}</Tag>
                ) : isLoading ? null : (
                  <Tag color="red">{t("overview.pendingCount", { count: rows.length })}</Tag>
                )}
                {!isLoading && (
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                    {t("overview.deadTasksSummary", {
                      types: new Intl.ListFormat(i18n.resolvedLanguage ?? "zh-CN", {
                        style: "narrow",
                        type: "conjunction",
                      }).format([...new Set(rows.map((r) => r.type))]),
                    })}
                  </Typography.Text>
                )}
              </Space>
            ),
            children: isLoading ? (
              <Skeleton active title={false} paragraph={{ rows: 1 }} />
            ) : (
              <>
                <BulkBar count={selected.length} onClear={() => setSelected([])}>
                  <ReasonAction
                    label={t("overview.replay")}
                    target={t("bulk.selected", { count: selected.length })}
                    title={t("overview.replayTitle")}
                    confirmText={t("bulk.replayConfirm", { count: selected.length })}
                    disabled={!writable}
                    disabledReason={t("overview.opsOnly")}
                    onSubmit={(reason) => bulk("retry", reason)}
                  />
                  <ReasonAction
                    label={t("overview.ignore")}
                    target={t("bulk.selected", { count: selected.length })}
                    title={t("overview.ignoreTitle")}
                    confirmText={t("bulk.ignoreConfirm", { count: selected.length })}
                    danger
                    disabled={!writable}
                    disabledReason={t("overview.opsOnly")}
                    onSubmit={(reason) => bulk("discard", reason)}
                  />
                </BulkBar>
                <Table<DeadTaskRow>
                  size="small"
                  rowKey="id"
                  pagination={false}
                  scroll={{ x: 860 }}
                  rowSelection={
                    writable
                      ? { selectedRowKeys: selected, onChange: (keys) => setSelected(keys.map(Number)) }
                      : undefined
                  }
                  locale={{
                    emptyText: (
                      <TableErrorEmpty
                        isError={isError}
                        isForbidden={isApiError(error) && error.status === 403}
                        onRetry={() => void refetch()}
                      >
                        {t("overview.noDeadTasks")}
                      </TableErrorEmpty>
                    ),
                  }}
                  dataSource={rows}
                  columns={[
                    { title: t("overview.colTask"), dataIndex: "type", width: 150 },
                    {
                      title: t("overview.colPayload"),
                      dataIndex: "payload",
                      render: (v: Record<string, unknown>) => (
                        <code style={{ fontSize: fontSize.caption }}>{JSON.stringify(v)}</code>
                      ),
                    },
                    { title: t("overview.colRetries"), dataIndex: "retries", width: 70, align: "right" },
                    {
                      title: t("overview.colLastError"),
                      dataIndex: "last_error",
                      render: (v: string | null) => (
                        <Tooltip title={<span style={{ whiteSpace: "pre-wrap" }}>{v ?? "-"}</span>}>
                          <span
                            style={{
                              color: adminColors.negative,
                              fontSize: fontSize.caption,
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
                        <RowActions
                          primary={
                            <ReasonAction
                              label={t("overview.replay")}
                              target={`#${r.id} · ${r.type}`}
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
                          }
                          more={
                            <RowMoreMenu>
                              <ReasonAction
                                label={t("overview.ignore")}
                                type="text"
                                target={`#${r.id} · ${r.type}`}
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
                            </RowMoreMenu>
                          }
                        />
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
        ]}
      />
    </div>
  );
}

/** Live alert stream: severity filter in the URL (?severity=), acknowledgement loop, deep links to the affected node/tenant (lib/alertLink); the polling cadence is given by the page. */
function AlertStreamCard({ refetchInterval }: { refetchInterval: number | false }) {
  const { t } = useTranslation(["admin", "shared"]);
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const navigate = useNavigate({ from: "/" });
  const { severity } = Route.useSearch();
  const { data, isError, refetch } = useAlerts({ limit: 50, ...(severity ? { severity } : {}) }, { refetchInterval });
  const ack = useAckAlertWithFeedback();
  const alerts: AlertRow[] = data?.items ?? [];

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
          onChange={(v: AlertSeverity | undefined) =>
            void navigate({ to: "/", replace: true, search: (prev) => ({ ...prev, severity: v }) })
          }
          options={SEVERITY_ORDER.map((s) => ({ value: s, label: t(severityMap[s].labelKey) }))}
        />
      }
      styles={{ body: { maxHeight: 560, overflow: "auto" } }}
    >
      {isError ? (
        <TableErrorEmpty compact isError onRetry={() => void refetch()} />
      ) : (
        <>
          {alerts.length === 0 && <EmptyState scene="notification" compact description={t("shell.noAlerts")} />}
          {alerts.map((a) => {
            const link = alertLink(a);
            return (
              <div key={a.id} style={{ marginBottom: 12 }}>
                <Space size={space.sm} align="start">
                  <StatusTag map={severityMap} value={a.severity} variant="text" icon />
                  {link ? (
                    <Link to={link.to} search={link.search}>
                      <b>{a.title}</b>
                    </Link>
                  ) : (
                    <b>{a.title}</b>
                  )}
                </Space>
                <div style={{ color: adminColors.textSecondary, fontSize: fontSize.caption, paddingLeft: 14 }}>
                  {formatDateTime(a.created_at)} · {a.content}
                </div>
                <div style={{ paddingLeft: 14, marginTop: 2 }}>
                  {a.acked_at ? (
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                      {t("overview.ackedBy", {
                        name: a.acked_by_username ?? `#${a.acked_by ?? "-"}`,
                        time: formatDateTime(a.acked_at),
                      })}
                    </Typography.Text>
                  ) : (
                    <GatedButton
                      size="small"
                      reason={writable ? undefined : t("overview.opsOnly")}
                      loading={ack.isPending && ack.variables.alertId === a.id}
                      onClick={() => ack.mutate({ alertId: a.id })}
                    >
                      {t("overview.ack")}
                    </GatedButton>
                  )}
                </div>
              </div>
            );
          })}
        </>
      )}
    </Card>
  );
}

/** Cursor page count: server total first, otherwise the loaded row count; no data = undefined. */
function pageCount(data: { pages: { items: unknown[]; total?: number | null }[] } | undefined): number | undefined {
  if (!data) return undefined;
  return data.pages[0]?.total ?? flattenPages(data).length;
}

/** Aggregate critical alerts, lost nodes, dead letters, settlement gaps and pending approvals. */
function useTriageItems({
  criticalUnacked,
  nodesMissing,
}: {
  criticalUnacked: number | undefined;
  nodesMissing: number | undefined;
}): TriageItem[] {
  const { t } = useTranslation();
  const role = useAdminRole();
  const canReadOps = canWriteOps(role) || role === "readonly";
  const canReadFinance = canWriteFinance(role) || role === "readonly";
  const deadQ = useDeadTasks({ enabled: canReadOps });
  const gapsQ = useSettlementGaps({ unresolved: true }, { enabled: canReadFinance });
  const refundsQ = useRefunds({ status: "pending" }, { enabled: canReadFinance });
  const invoicesQ = useInvoices({ status: "submitted" }, { enabled: canReadInvoices(role) });
  const deletionsQ = useDeletionRequests({ status: "pending" });
  const refunds = pageCount(refundsQ.data);
  const invoices = invoicesQ.data?.length;
  const deletions = deletionsQ.data?.length;
  const approvals =
    refunds !== undefined && invoices !== undefined && deletions !== undefined
      ? refunds + invoices + deletions
      : undefined;
  return [
    { key: "critical", label: t("overview.triage.critical"), count: criticalUnacked, severity: "critical" },
    { key: "missing", label: t("overview.triage.missingNodes"), count: nodesMissing, severity: "warning" },
    { key: "dead", label: t("overview.triage.deadTasks"), count: deadQ.data?.length, severity: "warning" },
    { key: "gaps", label: t("overview.triage.gaps"), count: pageCount(gapsQ.data), severity: "info" },
    {
      key: "approvals",
      label: t("overview.triage.approvals"),
      count: approvals,
      severity: "info",
      detail: t("overview.triage.approvalsDetail", {
        refunds: refunds ?? "—",
        invoices: invoices ?? "—",
        deletions: deletions ?? "—",
      }),
    },
  ];
}

const triageLinkStyle = { display: "inline-block", textDecoration: "none", color: "inherit" } as const;

/** Pending bar deep links: land on the prefiltered list by item.key; dead letters land on this page's anchor. */
function triageLink(item: TriageItem, children: ReactNode): ReactNode {
  switch (item.key) {
    case "critical":
      return (
        <Link to="/alerts" search={{ severity: "critical", acked: "unacked" }} style={triageLinkStyle}>
          {children}
        </Link>
      );
    case "missing":
      return (
        <Link to="/nodes" search={{ status: "Missing" }} style={triageLinkStyle}>
          {children}
        </Link>
      );
    case "dead":
      return (
        <a href="#dead-tasks" style={triageLinkStyle}>
          {children}
        </a>
      );
    case "gaps":
      return (
        <Link to="/finance" search={{ tab: "gaps" }} style={triageLinkStyle}>
          {children}
        </Link>
      );
    default:
      return (
        <Link to="/finance" search={{ tab: "refunds" }} style={triageLinkStyle}>
          {children}
        </Link>
      );
  }
}

/** Block-level whole-card link inheriting the text colour. */
const cardLinkStyle = { display: "block", textDecoration: "none", color: "inherit" } as const;

function Overview() {
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const qc = useQueryClient();
  const autoRefresh = useAutoRefresh(POLL.steady);
  const ovQ = useOverview({ refetchInterval: autoRefresh.refetchInterval });
  const unreadQ = useAlertUnreadCount({ refetchInterval: autoRefresh.refetchInterval });
  const revenueQ = useRevenueReport();
  const oversellQ = useOversellReport();
  const { data: ov } = ovQ;
  const { data: revenue } = revenueQ;
  const { data: unread } = unreadQ;
  const triage = useTriageItems({ criticalUnacked: unread?.critical_count, nodesMissing: ov?.nodes_missing });

  const oversellRows: OversellRow[] = oversellQ.data ?? [];
  const byStatus = ov?.instances_by_status ?? {};
  const activeInstances = (byStatus.creating ?? 0) + (byStatus.starting ?? 0) + (byStatus.running ?? 0);
  const signupDelta = revenue ? revenue.today_signups - revenue.yesterday_signups : 0;
  const revenueErr = revenueQ.isError ? <DataErrorAlert onRetry={() => void revenueQ.refetch()} /> : undefined;
  const ovErr = ovQ.isError ? <DataErrorAlert onRetry={() => void ovQ.refetch()} /> : undefined;
  const unreadErr = unreadQ.isError ? <DataErrorAlert onRetry={() => void unreadQ.refetch()} /> : undefined;
  const oversellBody = (chart: ReactNode) =>
    oversellQ.isError ? (
      <DataErrorAlert title={t("overview.loadFailed")} description={null} onRetry={() => void oversellQ.refetch()} />
    ) : oversellRows.length ? (
      chart
    ) : (
      <EmptyState scene="list" compact description={t("overview.oversellEmpty")} />
    );

  return (
    <PageContainer
      title={t("menu.overview")}
      freshness={{
        updatedAt: ovQ.dataUpdatedAt,
        intervalMs: autoRefresh.intervalMs,
        paused: autoRefresh.paused,
        onTogglePause: autoRefresh.toggle,
        onRefresh: () => {
          void ovQ.refetch();
          void qc.invalidateQueries({ queryKey: adminKeys.alerts });
        },
        refreshing: ovQ.isRefetching,
      }}
    >
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <TriageBar items={triage} renderLink={triageLink} ariaLabel={t("overview.triageAria")} />
        </Col>
        <Col span={24}>
          <KpiGrid
            items={[
              <StatCard
                key="rev-today"
                title={t("overview.todayRevenue")}
                value={revenue ? formatMoney(revenue.today_revenue) : undefined}
                error={revenueErr}
                footer={
                  revenue && (
                    <>
                      {t("overview.yesterdayPrefix", { amount: formatMoney(revenue.yesterday_revenue) })}
                      <br />
                      {t("overview.prepaidPart", { amount: formatMoney(revenue.today_prepaid) })}
                    </>
                  )
                }
                link={(c) => (
                  <Link to="/finance" style={cardLinkStyle}>
                    {c}
                  </Link>
                )}
              />,
              <StatCard
                key="rev-month"
                title={t("overview.monthRevenue")}
                value={revenue ? formatMoney(revenue.month_revenue) : undefined}
                error={revenueErr}
                footer={revenue && t("overview.prepaidPart", { amount: formatMoney(revenue.month_prepaid) })}
                link={(c) => (
                  <Link to="/finance" style={cardLinkStyle}>
                    {c}
                  </Link>
                )}
              />,
              <StatCard
                key="signup"
                title={t("overview.todaySignups")}
                value={revenue?.today_signups}
                error={revenueErr}
                trend={
                  revenue && {
                    text: t("overview.vsYesterday", { count: Math.abs(signupDelta) }),
                    direction: signupDelta > 0 ? "up" : signupDelta < 0 ? "down" : "flat",
                  }
                }
                link={(c) => (
                  <Link to="/tenants" style={cardLinkStyle}>
                    {c}
                  </Link>
                )}
              />,
              <StatCard
                key="paying"
                title={t("overview.payingTenants")}
                value={ov?.paying_tenants}
                error={ovErr}
                footer={ov && t("overview.tenantsTotalFooter", { count: ov.tenants_total })}
                link={(c) => (
                  <Link to="/tenants" style={cardLinkStyle}>
                    {c}
                  </Link>
                )}
              />,
            ]}
          />
        </Col>
        <Col span={24}>
          <KpiGrid
            items={[
              <StatCard
                key="active"
                title={t("overview.activeInstances")}
                value={ov ? activeInstances : undefined}
                error={ovErr}
                footer={
                  ov &&
                  t("overview.instanceStatusHint", { stopped: byStatus.stopped ?? 0, failed: byStatus.failed ?? 0 })
                }
                link={(c) => (
                  <Link to="/tenants" search={{ tab: "instances", istatus: "running" }} style={cardLinkStyle}>
                    {c}
                  </Link>
                )}
              />,
              <StatCard
                key="subs"
                title={t("overview.subscriptionsActive")}
                value={ov?.subscriptions_active}
                error={ovErr}
                footer={ov && t("overview.subscriptionsActiveHint")}
                link={(c) => (
                  <Link to="/tenants" search={{ tab: "instances" }} style={cardLinkStyle}>
                    {c}
                  </Link>
                )}
              />,
              <StatCard
                key="nodes"
                title={t("overview.nodesHealth")}
                value={ov ? `${ov.nodes_ready} / ${ov.nodes_total}` : undefined}
                error={ovErr}
                footer={
                  ov && (
                    <span style={{ color: ov.nodes_missing > 0 ? adminColors.negative : undefined }}>
                      {t("overview.nodesMissing", { count: ov.nodes_missing })}
                    </span>
                  )
                }
                link={(c) => (
                  <Link to="/nodes" style={cardLinkStyle}>
                    {c}
                  </Link>
                )}
              />,
              <StatCard
                key="alerts"
                title={t("overview.alertsTotal")}
                value={unread?.count}
                error={unreadErr}
                tone={(unread?.critical_count ?? 0) > 0 ? "negative" : "default"}
                link={(c) => (
                  <Link to="/alerts" search={{ acked: "unacked" }} style={cardLinkStyle}>
                    {c}
                  </Link>
                )}
              />,
            ]}
          />
        </Col>

        <Col xs={24} xl={16}>
          <Card
            title={t("overview.oversellChartTitle")}
            extra={
              <Tooltip title={t("overview.oversellRule")}>
                <QuestionCircleOutlined
                  tabIndex={0}
                  className="focus-ring"
                  aria-label={t("overview.oversellRule")}
                  style={{ fontSize: iconSize.sm, color: adminColors.textSecondary, cursor: "help" }}
                />
              </Tooltip>
            }
          >
            {oversellBody(<OversellRatioChart rows={oversellRows} />)}
          </Card>
          <Card title={t("overview.utilChartTitle")} style={{ marginTop: 16 }}>
            {oversellBody(<UtilChart rows={oversellRows} />)}
          </Card>
          <Card title={t("overview.poolOccupancy")} style={{ marginTop: 16 }}>
            {ovQ.isError ? (
              <DataErrorAlert title={t("overview.loadFailed")} description={null} onRetry={() => void ovQ.refetch()} />
            ) : ov && ov.pools.length ? (
              <PoolOccupancy pools={ov.pools} />
            ) : (
              <EmptyState scene="list" compact description={t("overview.poolEmpty")} />
            )}
          </Card>
          <DeadTasksCard />
        </Col>
        <Col xs={24} xl={8}>
          <AlertStreamCard refetchInterval={autoRefresh.refetchInterval} />
        </Col>
      </Row>
    </PageContainer>
  );
}
