/** 实例详情:监控(降级文案)/连接/日志/事件时间线(=计费依据)/账单 + 危险区释放。
 *  事件/账单两 Tab 走游标分页;面包屑返回列表不丢筛选态。
 *  在线服务的版本实例不进列表,但直链可达:「连接」按 with_ssh 决定出不出 SSH 卡,Jupyter 卡不出。 */

import { type InstanceEventOut, type InstanceOut } from "@superdl/api-client";
import { formatDateTime, isTransientInstanceStatus, localToday } from "@superdl/ui";
import { DataErrorAlert, moneyOr, useConfirm } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  Alert,
  App,
  Breadcrumb,
  Button,
  Card,
  Descriptions,
  Skeleton,
  Space,
  Tabs,
  theme,
  Tooltip,
  Typography,
} from "antd";
import { useFormat } from "@superdl/ui";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useResetJupyterToken } from "../api/mutations";
import {
  useDailySummary,
  useHourlyBillPages,
  useInstance,
  useInstanceAccess,
  useInstanceEventPages,
  useInstanceLogs,
} from "../api/queries";
import {
  CopyButton,
  InstanceStatusBadge,
  SpotTag,
  SubscriptionTag,
  TierTag,
} from "../components/common";
import { HourlyBillsTable } from "../components/HourlyBillsTable";
import { EventsPanel } from "../components/instance/EventsPanel";
import { LogsPanel } from "../components/instance/LogsPanel";
import { MetricsPanel } from "../components/instance/MetricsPanel";
import { InstanceActions, ReleaseModal, canReleaseStatus } from "../components/InstanceActions";
import { requireAuth } from "../lib/guard";

// 旧链接的 ?tab=service 不在白名单里,validateSearch 剥离后回默认 Tab
const DETAIL_TABS = ["metrics", "access", "logs", "events", "bills"] as const;

export const Route = createFileRoute("/_console/instances_/$uuid")({
  beforeLoad: requireAuth,
  validateSearch: (search: Record<string, unknown>): { tab?: string } => {
    // tab 白名单:非法值回退默认 Tab,不渲染无选中态的 Tabs
    const tab = search["tab"];
    return typeof tab === "string" && (DETAIL_TABS as readonly string[]).includes(tab)
      ? { tab }
      : {};
  },
  component: InstanceDetail,
});

/** 连接:SSH 卡按 with_ssh 出,Jupyter 卡只对开发机出。
 *  接入信息的每个字段都可空,必须按「拿到什么渲染什么」写。 */
function AccessTab({ instance, running }: { instance: InstanceOut; running: boolean }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const { data: access } = useInstanceAccess(instance.uuid, { enabled: running });
  const reset = useResetJupyterToken();
  const isService = instance.workload_type === "service";
  if (!running) {
    return <Alert type="info" showIcon title={t("instances.accessNotRunning")} />;
  }
  // 服务型 + 不开 SSH:这个 Tab 没有任何入口,直接把人指到「服务」Tab,不留一张空卡
  if (isService && !instance.with_ssh) {
    return <Alert type="info" showIcon title={t("instances.accessServiceOnly")} />;
  }
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      {instance.with_ssh && (
        <Card size="small" title="SSH">
          <Space orientation="vertical">
            <Typography.Text code>{access?.ssh_command}</Typography.Text>
            {access?.ssh_command && (
              <CopyButton text={access.ssh_command} label={t("instances.copyCommand")} />
            )}
            <Typography.Text type="secondary">{t("copy.sshKeyOnly")}</Typography.Text>
          </Space>
        </Card>
      )}
      {!isService && (
        <Card size="small" title="JupyterLab">
          <Space>
            <Button
              type="primary"
              disabled={!access?.jupyter_url}
              onClick={() => {
                if (access?.jupyter_url) {
                  window.open(access.jupyter_url, "_blank", "noopener,noreferrer");
                }
              }}
            >
              {t("instances.openJupyter")}
            </Button>
            <Button
              onClick={() =>
                confirm({
                  title: t("instances.resetTokenConfirmTitle"),
                  consequences: [t("instances.resetTokenConfirmBody")],
                  onOk: async () => {
                    await reset.mutateAsync(instance.uuid);
                    message.success(t("instances.tokenReset"));
                  },
                })
              }
            >
              {t("instances.resetToken")}
            </Button>
          </Space>
        </Card>
      )}
    </Space>
  );
}

function LogsTab({ uuid, viewable }: { uuid: string; viewable: boolean }) {
  const [tail, setTail] = useState(200);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const { data, error, refetch } = useInstanceLogs(
    uuid,
    { tail_lines: tail },
    // 页面不可见时间隔轮询自动暂停(react-query 默认,未开 refetchIntervalInBackground)
    { enabled: viewable, refetchInterval: autoRefresh ? 10_000 : false, retry: 0 },
  );
  const lines = useMemo(() => data?.lines ?? [], [data]);
  return (
    <LogsPanel
      viewable={viewable}
      lines={lines}
      truncated={data?.truncated}
      error={error}
      onRetry={() => void refetch()}
      tail={tail}
      onTail={setTail}
      autoRefresh={autoRefresh}
      onAutoRefresh={setAutoRefresh}
      downloadName={uuid}
    />
  );
}

function EventsTab({ uuid, status }: { uuid: string; status?: string }) {
  const queryClient = useQueryClient();
  // 时间线即计费依据:过渡态必须跟着状态一起刷新;游标分页 + 加载更多。
  // 轮询三律③:infinite 查询不挂 refetchInterval;事件只在状态迁移时产生,
  // 外层实例轮询(过渡态 5s)检测到 status 迁移后失效事件查询 —— 立即 + 3s 延迟各刷一次,
  // 覆盖「事件行落库略晚于实例行状态翻转」的窗口。
  const prevStatus = useRef(status);
  useEffect(() => {
    if (prevStatus.current === status) return;
    prevStatus.current = status;
    const key = ["instances", uuid, "events"];
    void queryClient.invalidateQueries({ queryKey: key });
    const timer = setTimeout(() => void queryClient.invalidateQueries({ queryKey: key }), 3_000);
    return () => clearTimeout(timer);
  }, [status, uuid, queryClient]);
  const {
    data,
    isLoading,
    isError,
    refetch,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
    fetchNextPage,
  } = useInstanceEventPages(uuid);
  const events = useMemo<InstanceEventOut[]>(
    () => (data?.pages ?? []).flatMap((p) => p.items),
    [data],
  );
  return (
    <EventsPanel
      events={events}
      isLoading={isLoading}
      isError={isError}
      onRetry={() => void refetch()}
      hasNextPage={hasNextPage ?? false}
      isFetchingNextPage={isFetchingNextPage}
      isFetchNextPageError={isFetchNextPageError}
      onLoadMore={() => void fetchNextPage()}
    />
  );
}

function BillsTab({ instanceId }: { instanceId: number }) {
  return <HourlyBillsTable query={useHourlyBillPages({ instance_id: instanceId })} />;
}


function InstanceDetail() {
  const { t } = useTranslation();
  const { formatHourlyPrice, formatMoney, formatPeriodPrice } = useFormat();
  const { token } = theme.useToken();
  const { uuid } = Route.useParams();
  const { tab } = Route.useSearch();
  const navigate = useNavigate();
  const [releaseOpen, setReleaseOpen] = useState(false);
  const {
    data: instance,
    isError: instanceError,
    refetch: refetchInstance,
  } = useInstance(uuid, {
    refetchInterval: (q) =>
      q.state.data && isTransientInstanceStatus(q.state.data.status) ? 5_000 : 30_000,
  });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: 60_000 });
  const todayAmount =
    (instance && daily?.items.find((it) => it.instance_id === instance.id)?.total_amount) ?? null;

  if (instanceError && !instance) {
    return <DataErrorAlert onRetry={() => void refetchInstance()} />;
  }
  // 首载骨架,不留白屏
  if (!instance) {
    return (
      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
        <Card>
          <Skeleton active title={{ width: 240 }} paragraph={{ rows: 2 }} />
        </Card>
        <Card>
          <Skeleton active paragraph={{ rows: 6 }} />
        </Card>
      </Space>
    );
  }
  const running = instance.status === "running";
  const canRelease = canReleaseStatus(instance.status);
  const activeTab = tab ?? "metrics";

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      {/* 面包屑:列表筛选态在 URL 上,返回列表不丢 */}
      <Breadcrumb
        items={[
          {
            title: <Link to="/instances">{t("instances.title")}</Link>,
          },
          { title: instance.name },
        ]}
      />
      <Card>
        <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
          <Space orientation="vertical" size={4}>
            <Space>
              <Typography.Title level={4} style={{ margin: 0 }}>
                {instance.name}
              </Typography.Title>
              <InstanceStatusBadge
                status={instance.status}
                frozenDeadline={instance.frozen_deadline}
              />
              <TierTag tier={instance.spec["tier"] as string} pool={instance.spec["pool_label"] as string} />
              <SubscriptionTag market={instance.market} subscription={instance.subscription} />
              <SpotTag market={instance.market} />
            </Space>
            <Descriptions
              size="small"
              column={{ xs: 1, sm: 2, md: 3, xl: 4 }}
              items={[
                { label: t("instances.labelId"), children: instance.uuid.slice(0, 12) },
                {
                  label: t("instances.labelSpec"),
                  children: t("instances.specLine", { model: instance.spec["gpu_model"] as string, count: instance.gpu_count }),
                },
                {
                  label: t("instances.labelBilling"),
                  // 包周期实例的时价是折后价、且不出小时账,报「¥X/时 × N 卡」会让人以为在按小时扣
                  children: instance.subscription
                    ? formatPeriodPrice(
                        instance.subscription.amount_paid,
                        instance.subscription.period,
                        instance.subscription.period_count,
                      )
                    : t("instances.pricePerCard", { price: formatHourlyPrice(instance.price_hourly), count: instance.gpu_count }),
                },
                ...(instance.subscription
                  ? [
                      {
                        label: t("instances.labelExpiresAt"),
                        children: formatDateTime(instance.subscription.expires_at),
                      },
                    ]
                  : [
                      {
                        label: t("instances.labelToday"),
                        children: moneyOr(formatMoney(todayAmount), daily != null),
                      },
                    ]),
                { label: t("instances.createdAt"), children: formatDateTime(instance.created_at) },
              ]}
            />
          </Space>
          {/* 详情页的「事件日志」跳到本页事件 Tab(不传 onShowEvents 即死按钮) */}
          <InstanceActions
            instance={instance}
            onShowEvents={() =>
              void navigate({ to: "/instances/$uuid", params: { uuid }, search: { tab: "events" } })
            }
          />
        </Space>
      </Card>

      <Tabs
        activeKey={activeTab}
        onChange={(k) =>
          // Tab activeKey 入 URL 用 replace(ui-ux-spec §1-8):连点六个 Tab 后退一次就该回列表
          navigate({ to: "/instances/$uuid", params: { uuid }, search: { tab: k }, replace: true })
        }
        items={[
          {
            key: "metrics",
            label: t("instances.tabMetrics"),
            children: <MetricsPanel uuid={uuid} running={running} />,
          },
          {
            key: "access",
            label: t("instances.tabAccess"),
            children: <AccessTab instance={instance} running={running} />,
          },
          {
            key: "logs",
            label: t("instances.tabLogs"),
            children: (
              <LogsTab
                uuid={uuid}
                viewable={instance.status === "running" || instance.status === "stopping"}
              />
            ),
          },
          {
            key: "events",
            label: t("instances.tabEvents"),
            children: <EventsTab uuid={uuid} status={instance?.status} />,
          },
          {
            key: "bills",
            label: t("instances.tabBills"),
            children: <BillsTab instanceId={instance.id} />,
          },
        ]}
      />

      <Card title={t("instances.dangerZone")} style={{ borderColor: token.colorErrorBorder }}>
        <Space orientation="vertical">
          <Typography.Text type="secondary">
            {t("instances.dangerNote")}
          </Typography.Text>
          {/* 禁用原因走 Tooltip 不用原生 title:disabled 按钮在部分浏览器不触发 mouse 事件,
              title 不可达(全站其它禁用项同一处理) */}
          <Tooltip title={canRelease ? undefined : t("copy.releaseNeedsStopped")}>
            <Button danger disabled={!canRelease} onClick={() => setReleaseOpen(true)}>
              {t("instances.release")}
            </Button>
          </Tooltip>
        </Space>
      </Card>
      <ReleaseModal
        instance={instance}
        open={releaseOpen}
        onClose={() => setReleaseOpen(false)}
        onReleased={() => navigate({ to: "/instances" })}
      />
    </Space>
  );
}
