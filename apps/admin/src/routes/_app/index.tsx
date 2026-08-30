import { adminColors, fontSize, formatDateTime, statusColors } from "@superdl/ui";
import { DataErrorAlert, EChart, KpiGrid, PageContainer, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Row,
  Select,
  Skeleton,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";

import {
  type AlertRow,
  type DeadTaskRow,
  type OversellRow,
  type OverviewOut,
  isApiError,
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
import { alertLink, SEVERITY_LABEL_KEY, severityColor, useAckAlertWithFeedback } from "../../lib/alertLink";
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
  return (
    <EChart
      option={option}
      style={{ height: 320 }}
      theme="noc"
      ariaLabel={t("overview.oversellChartTitle")}
    />
  );
}

function PoolOccupancy({ pools }: { pools: OverviewOut["pools"] }) {
  const { t } = useTranslation();
  const names = pools.map((p) => p.pool);
  // 「已租」拆两段:竞价那段与空闲一起才是真正的可调度余量。
  // gpu_spot_used 服务端已按 gpu_used 截断,前端不许 clamp 第二遍,否则会吃掉后端口径的变化
  const spotUsed = pools.map((p) => p.gpu_spot_used);
  const usedOther = pools.map((p) => p.gpu_used - p.gpu_spot_used);
  // 空闲只算 Ready 节点的卡;非 Ready 节点的物理卡画成第三段,不混进「空闲」
  const free = pools.map((p) => Math.max(0, p.ready_gpu_total - p.gpu_used));
  const notReady = pools.map((p) => Math.max(0, p.gpu_total - p.ready_gpu_total));
  const usedTotal = pools.reduce((n, p) => n + p.gpu_used, 0);
  const spotTotal = pools.reduce((n, p) => n + p.gpu_spot_used, 0);
  const option = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: adminColors.textSecondary } },
    grid: { left: 80, right: 24, top: 32, bottom: 28 },
    xAxis: { type: "value", axisLabel: { color: adminColors.textSecondary }, splitLine: { lineStyle: { color: adminColors.gridLine } } },
    yAxis: { type: "category", data: names, axisLabel: { color: adminColors.textSecondary } },
    series: [
      { name: t("overview.rented"), type: "bar", stack: "t", data: usedOther, itemStyle: { color: statusColors.green } },
      // 竞价段紧挨已租段,取 marketMap.spot 的橙:两端「竞价」是同一个颜色
      { name: t("overview.rentedSpot"), type: "bar", stack: "t", data: spotUsed, itemStyle: { color: statusColors.orange } },
      { name: t("overview.idle"), type: "bar", stack: "t", data: free, itemStyle: { color: adminColors.chartNeutral } },
      { name: t("overview.notReady"), type: "bar", stack: "t", data: notReady, itemStyle: { color: adminColors.alertAccent } },
    ],
  };
  return (
    <>
      <EChart option={option} style={{ height: 220 }} theme="noc" ariaLabel={t("overview.poolOccupancy")} />
      {/* 图例里两段是并排的,合计只能靠这句话讲清楚:与收入卡「其中包周期预付」同一写法 */}
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {t("overview.spotReclaimable", { used: usedTotal, spot: spotTotal })}
      </Typography.Text>
    </>
  );
}

/** 值班首屏第二排:任务死信(重放/忽略都需原因 + 二次确认,handler 幂等)。 */
function DeadTasksCard() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  // 读死信需 ops/readonly:finance 看不到这张卡,也不发会 403 的轮询
  const canRead = canWriteOps(role) || role === "readonly";
  const { data, queryKey, isLoading, isError, error, refetch } = useDeadTasks({ enabled: canRead });
  const rows: DeadTaskRow[] = data ?? [];
  const retry = useRetryDeadTask();
  const discard = useDiscardDeadTask();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  // 查询失败也要露出(值班首屏「没有死信」是最危险的误判),错误态由表内空态明示;
  // 首响未到先渲染一行骨架占位(卡片 pop-in 会把下方图表顶下去)
  if (!canRead) return null;
  if (!isError && !isLoading && rows.length === 0) return null;
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
              {isError ? (
                <Tag color="orange">{t("common.loadFailed")}</Tag>
              ) : isLoading ? null : (
                <Tag color="red">{t("overview.pendingCount", { count: rows.length })}</Tag>
              )}
              {!isLoading && (
                <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                  {t("overview.deadTasksSummary", {
                    // 列表串联走 Intl.ListFormat(随界面语言给「、」/「, 」,en 界面不出 CJK 顿号)
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
      <Table<DeadTaskRow>
        size="small"
        rowKey="id"
        pagination={false}
        scroll={{ x: 860 }}
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

/** 实时告警流:severity 过滤、确认闭环(留确认人+时间)、点击跳受影响节点/租户(深链与顶栏铃铛共用 lib/alertLink)。 */
function AlertStreamCard() {
  const { t } = useTranslation();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const [severity, setSeverity] = useState<string | undefined>();
  const { data, isError, refetch } = useAlerts(severity ? { severity } : undefined);
  const ack = useAckAlertWithFeedback();
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
          options={(Object.keys(SEVERITY_LABEL_KEY) as (keyof typeof SEVERITY_LABEL_KEY)[]).map((s) => ({
            value: s,
            label: t(SEVERITY_LABEL_KEY[s]),
          }))}
        />
      }
      styles={{ body: { maxHeight: 560, overflow: "auto" } }}
    >
      {/* 查询失败绝不能渲染成「暂无告警」(值班会把故障误读成天下太平) */}
      {isError ? (
        <TableErrorEmpty compact isError onRetry={() => void refetch()} />
      ) : (
        <>
          {alerts.length === 0 && <Empty description={t("shell.noAlerts")} />}
          {alerts.map((a) => {
            const link = alertLink(a);
            return (
              <div key={a.id} style={{ marginBottom: 12 }}>
                <Badge
                  color={severityColor(a.severity)}
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
        </>
      )}
    </Card>
  );
}

/** 取数失败要显式说:渲染成「暂无数据」等于把集群不可达伪装成没数据(总览端点全角色可读,无 403 分支)。 */
function LoadFailed({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <Alert
      type="error"
      showIcon
      title={t("overview.loadFailed")}
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
  const oversellQ = useOversellReport();
  // 总览聚合:全部精确 COUNT(全角色可读),不从截断列表推算
  const ovQ = useOverview();
  const revenueQ = useRevenueReport();
  // 「告警(总)」 = 未确认告警精确计数(独立计数端点;截断的告警流长度会低估)
  const unreadQ = useAlertUnreadCount();
  const { data: oversell, isError: oversellError, refetch: refetchOversell } = oversellQ;
  const { data: ov, isError: ovError, refetch: refetchOv } = ovQ;
  const { data: revenue } = revenueQ;
  const { data: unread } = unreadQ;

  const oversellRows: OversellRow[] = oversell ?? [];
  const byStatus = ov?.instances_by_status ?? {};
  const activeInstances =
    (byStatus.creating ?? 0) + (byStatus.starting ?? 0) + (byStatus.running ?? 0);
  const signupDelta = revenue ? revenue.today_signups - revenue.yesterday_signups : 0;
  // KPI 查询失败必须嵌错误条而非 "—" 假阴性;按数据源分卡归属
  const revenueErr = <DataErrorAlert onRetry={() => void revenueQ.refetch()} />;
  const ovErr = <DataErrorAlert onRetry={() => void ovQ.refetch()} />;
  const unreadErr = <DataErrorAlert onRetry={() => void unreadQ.refetch()} />;

  return (
    <PageContainer title={t("menu.overview")}>
    <Row gutter={[16, 16]}>
      <Col span={24}>
        <KpiGrid
          loading={revenueQ.isLoading || ovQ.isLoading}
          items={[
            <Card key="rev-today">
              {revenueQ.isError ? revenueErr : (
                <>
                  <Statistic title={t("overview.todayRevenue")} value={revenue ? formatMoney(revenue.today_revenue) : "—"} />
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption, display: "block" }}>
                    {t("overview.yesterdayPrefix", { amount: revenue ? formatMoney(revenue.yesterday_revenue) : "—" })}
                  </Typography.Text>
                  {/* 收入已含包周期预付,必须摊开单列:一笔包年当天就是尖峰,不标出来昨日环比会被读成异常 */}
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                    {t("overview.prepaidPart", { amount: revenue ? formatMoney(revenue.today_prepaid) : "—" })}
                  </Typography.Text>
                </>
              )}
            </Card>,
            <Card key="rev-month">
              {revenueQ.isError ? revenueErr : (
                <>
                  <Statistic title={t("overview.monthRevenue")} value={revenue ? formatMoney(revenue.month_revenue) : "—"} />
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                    {t("overview.prepaidPart", { amount: revenue ? formatMoney(revenue.month_prepaid) : "—" })}
                  </Typography.Text>
                </>
              )}
            </Card>,
            <Card key="signup">
              {revenueQ.isError ? revenueErr : (
                <>
                  <Statistic title={t("overview.todaySignups")} value={revenue ? revenue.today_signups : "—"} />
                  <Typography.Text
                    style={{ fontSize: fontSize.caption, color: signupDelta >= 0 ? adminColors.positive : adminColors.negative }}
                  >
                    {signupDelta >= 0 ? "▲" : "▼"} {t("overview.vsYesterday", { count: Math.abs(signupDelta) })}
                  </Typography.Text>
                </>
              )}
            </Card>,
            <Card key="alerts">
              {unreadQ.isError ? unreadErr : (
                <Statistic
                  title={t("overview.alertsTotal")}
                  value={unread?.count ?? "—"}
                  styles={{
                    // 红色高亮用精确计数端点的 critical 口径:截断的告警流列表会漏报
                    content: (unread?.critical_count ?? 0) > 0
                      ? { color: adminColors.negative }
                      : undefined,
                  }}
                />
              )}
            </Card>,
            <Card key="active">
              {ovQ.isError ? ovErr : (
                <>
                  <Statistic title={t("overview.activeInstances")} value={ov ? activeInstances : "—"} />
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                    {t("overview.instanceStatusHint", {
                      stopped: byStatus.stopped ?? 0,
                      failed: byStatus.failed ?? 0,
                    })}
                  </Typography.Text>
                </>
              )}
            </Card>,
            <Card key="subs">
              {ovQ.isError ? ovErr : (
                <>
                  {/* 按订阅行数而非实例状态数:停机的包月实例仍在保仍占库存,这个数可以大于活跃实例数 */}
                  <Statistic
                    title={t("overview.subscriptionsActive")}
                    value={ov ? ov.subscriptions_active : "—"}
                  />
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                    {t("overview.subscriptionsActiveHint")}
                  </Typography.Text>
                </>
              )}
            </Card>,
            <Card key="paying">
              {ovQ.isError ? ovErr : (
                <Statistic
                  title={t("overview.payingTenants")}
                  value={ov ? `${ov.paying_tenants} / ${ov.tenants_total}` : "—"}
                />
              )}
            </Card>,
            <Card key="nodes">
              {ovQ.isError ? ovErr : (
                <>
                  <Statistic
                    title={t("overview.nodesHealth")}
                    value={ov ? `${ov.nodes_ready} / ${ov.nodes_total}` : "—"}
                  />
                  <Typography.Text
                    style={{
                      fontSize: fontSize.caption,
                      color: ov && ov.nodes_missing > 0 ? adminColors.negative : adminColors.textSecondary,
                    }}
                  >
                    {t("overview.nodesMissing", { count: ov?.nodes_missing ?? 0 })}
                  </Typography.Text>
                </>
              )}
            </Card>,
          ]}
        />
      </Col>

      <DeadTasksCard />

      <Col xs={24} xl={16}>
        <Card
          title={t("overview.oversellChartTitle")}
          extra={<Typography.Text type="secondary">{t("overview.oversellHint")}</Typography.Text>}
        >
          {oversellError ? (
            <LoadFailed onRetry={() => void refetchOversell()} />
          ) : oversellRows.length ? (
            <OversellChart rows={oversellRows} />
          ) : (
            <Empty description={t("overview.oversellEmpty")} />
          )}
        </Card>
        <Card title={t("overview.poolOccupancy")} style={{ marginTop: 16 }}>
          {ovError ? (
            <LoadFailed onRetry={() => void refetchOv()} />
          ) : ov && ov.pools.length ? (
            <PoolOccupancy pools={ov.pools} />
          ) : (
            <Empty description={t("overview.poolEmpty")} />
          )}
        </Card>
      </Col>
      <Col xs={24} xl={8}>
        <AlertStreamCard />
      </Col>
    </Row>
    </PageContainer>
  );
}
