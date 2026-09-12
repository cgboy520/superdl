/** 实例详情:连接 / 监控 / 日志 / 事件时间线(= 计费依据)/ 账单 / 设置(改名 + 危险区释放)。默认 Tab 按状态(running → 连接,其余 → 事件);事件/账单 Tab 游标分页;面包屑返回列表不丢筛选态。服务的版本实例不进列表但直链可达:「连接」按 with_ssh 出 SSH 卡,Jupyter 卡不出。 */

import { type InstanceOut } from "@superdl/api-client";
import { controlWidth, flattenPages, formatDateTime, isTransientInstanceStatus, localToday, POLL } from "@superdl/ui";
import { DangerZone, DataErrorAlert, moneyOr, useConfirm } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Alert, App, Breadcrumb, Button, Card, Descriptions, Input, Skeleton, Space, Tabs, Typography } from "antd";
import { useFormat } from "@superdl/ui";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { keys } from "../api/keys";
import { useRenameInstance, useResetJupyterToken } from "../api/mutations";
import {
  useDailySummary,
  useHourlyBillPages,
  useInstance,
  useInstanceAccess,
  useInstanceEventPages,
  useInstanceLogs,
} from "../api/queries";
import { CopyButton, InstanceStatusBadge, SpotTag, SubscriptionTag, TierTag } from "../components/common";
import { HourlyBillsTable } from "../components/HourlyBillsTable";
import { EventsPanel } from "../components/instance/EventsPanel";
import { LogsPanel } from "../components/instance/LogsPanel";
import { MetricsPanel } from "../components/instance/MetricsPanel";
import { InstanceActions, ReleaseModal, canReleaseStatus } from "../components/InstanceActions";
import { requireAuth } from "../lib/guard";
import { useRememberedListSearch } from "../stores/listSearch";

// 旧链接的 ?tab=service 不在白名单,回默认 Tab
const DETAIL_TABS = ["access", "metrics", "logs", "events", "bills", "settings"] as const;

export const Route = createFileRoute("/_console/instances_/$uuid")({
  beforeLoad: requireAuth,
  validateSearch: (search: Record<string, unknown>): { tab?: string } => {
    // tab 白名单:非法值回默认 Tab
    const tab = search.tab;
    return typeof tab === "string" && (DETAIL_TABS as readonly string[]).includes(tab) ? { tab } : {};
  },
  component: InstanceDetail,
});

/** 连接:SSH 卡按 with_ssh 出,Jupyter 卡只对开发机出;接入信息字段可空,拿到什么渲染什么。 */
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
  // 服务型 + 不开 SSH:指到「服务」Tab
  if (isService && !instance.with_ssh) {
    return <Alert type="info" showIcon title={t("instances.accessServiceOnly")} />;
  }
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      {instance.with_ssh && (
        <Card size="small" title="SSH">
          <Space orientation="vertical">
            <Typography.Text code>{access?.ssh_command}</Typography.Text>
            {access?.ssh_command && <CopyButton text={access.ssh_command} label={t("instances.copyCommand")} />}
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
    // 页面不可见时间隔轮询自动暂停(未开 refetchIntervalInBackground)
    { enabled: viewable, refetchInterval: autoRefresh ? POLL.logs : false, retry: 0 },
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
  // 事件时间线游标分页,不挂 refetchInterval;外层实例轮询检测到 status 迁移后失效事件查询(立即 + 3s 延迟各一次)
  const prevStatus = useRef(status);
  useEffect(() => {
    if (prevStatus.current === status) return;
    prevStatus.current = status;
    const key = keys.instances.events(uuid);
    void queryClient.invalidateQueries({ queryKey: key });
    const timer = setTimeout(() => void queryClient.invalidateQueries({ queryKey: key }), 3_000);
    return () => clearTimeout(timer);
  }, [status, uuid, queryClient]);
  const { data, isLoading, isError, refetch, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } =
    useInstanceEventPages(uuid);
  const events = useMemo(() => flattenPages(data), [data]);
  return (
    <EventsPanel
      events={events}
      isLoading={isLoading}
      isError={isError}
      onRetry={() => void refetch()}
      hasNextPage={hasNextPage}
      isFetchingNextPage={isFetchingNextPage}
      isFetchNextPageError={isFetchNextPageError}
      onLoadMore={() => void fetchNextPage()}
    />
  );
}

function BillsTab({ instanceId }: { instanceId: number }) {
  return <HourlyBillsTable query={useHourlyBillPages({ instance_id: instanceId })} />;
}

/** 设置 Tab:改名 + 危险区(释放),与服务详情的设置 Tab 对齐。 */
function SettingsTab({
  instance,
  canRelease,
  onRelease,
}: {
  instance: InstanceOut;
  canRelease: boolean;
  onRelease: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [name, setName] = useState(instance.name);
  const rename = useRenameInstance();
  const dirty = name.trim() !== "" && name.trim() !== instance.name;
  const save = async () => {
    await rename.mutateAsync({ uuid: instance.uuid, name: name.trim() });
    message.success(t("instances.renamed"));
  };
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Card size="small" title={t("instances.settingsBasic")}>
        <Space wrap>
          <Input
            value={name}
            maxLength={64}
            aria-label={t("create.nameLabel")}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setName(e.target.value)}
            style={{ width: controlWidth.lg }}
          />
          <Button type="primary" disabled={!dirty} loading={rename.isPending} onClick={() => void save()}>
            {t("instances.saveName")}
          </Button>
        </Space>
      </Card>
      <DangerZone
        title={t("instances.dangerZone")}
        description={t("instances.dangerNote")}
        actions={[
          {
            key: "release",
            label: t("instances.release"),
            onClick: onRelease,
            disabled: !canRelease,
            disabledReason: t("copy.releaseNeedsStopped"),
          },
        ]}
      />
    </Space>
  );
}

function InstanceDetail() {
  const { t } = useTranslation();
  const { formatHourlyPrice, formatMoney, formatPeriodPrice } = useFormat();
  const { uuid } = Route.useParams();
  const { tab } = Route.useSearch();
  const navigate = useNavigate();
  const [releaseOpen, setReleaseOpen] = useState(false);
  const listSearch = useRememberedListSearch("/instances") as { q?: string; status?: string };
  const {
    data: instance,
    isError: instanceError,
    refetch: refetchInstance,
  } = useInstance(uuid, {
    refetchInterval: (q) =>
      q.state.data && isTransientInstanceStatus(q.state.data.status) ? POLL.transient : POLL.steady,
  });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: POLL.daily });
  const todayAmount = (instance && daily?.items.find((it) => it.instance_id === instance.id)?.total_amount) ?? null;

  if (instanceError && !instance) {
    return <DataErrorAlert onRetry={() => void refetchInstance()} />;
  }
  // 首载骨架
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
  // 默认 Tab 按状态:running 最想做的是连接;其余(停机 / 失败 / 过渡态)看事件最有用
  const activeTab = tab ?? (running ? "access" : "events");

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      {/* 面包屑:带回列表页最近一次筛选态(stores/listSearch) */}
      <Breadcrumb
        items={[
          {
            title: (
              <Link to="/instances" search={listSearch}>
                {t("instances.title")}
              </Link>
            ),
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
              <InstanceStatusBadge status={instance.status} frozenDeadline={instance.frozen_deadline} />
              <TierTag tier={instance.spec.tier as string} pool={instance.spec.pool_label as string} />
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
                  children: t("instances.specLine", {
                    model: instance.spec.gpu_model as string,
                    count: instance.gpu_count,
                  }),
                },
                {
                  label: t("instances.labelBilling"),
                  // 包周期实例时价是折后价且不出小时账,不报「¥X/时 × N 卡」
                  children: instance.subscription
                    ? formatPeriodPrice(
                        instance.subscription.amount_paid,
                        instance.subscription.period,
                        instance.subscription.period_count,
                      )
                    : t("instances.pricePerCard", {
                        price: formatHourlyPrice(instance.price_hourly),
                        count: instance.gpu_count,
                      }),
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
          {/* 「事件日志」跳到本页事件 Tab */}
          <InstanceActions
            instance={instance}
            size="middle"
            onShowEvents={() => void navigate({ to: "/instances/$uuid", params: { uuid }, search: { tab: "events" } })}
          />
        </Space>
      </Card>

      <Tabs
        activeKey={activeTab}
        onChange={(k) =>
          // Tab activeKey 入 URL 用 replace(ui-ux-spec §1-8)
          void navigate({ to: "/instances/$uuid", params: { uuid }, search: { tab: k }, replace: true })
        }
        items={[
          {
            key: "access",
            label: t("instances.tabAccess"),
            children: <AccessTab instance={instance} running={running} />,
          },
          {
            key: "metrics",
            label: t("instances.tabMetrics"),
            children: <MetricsPanel uuid={uuid} running={running} />,
          },
          {
            key: "logs",
            label: t("instances.tabLogs"),
            children: (
              <LogsTab uuid={uuid} viewable={instance.status === "running" || instance.status === "stopping"} />
            ),
          },
          {
            key: "events",
            label: t("instances.tabEvents"),
            children: <EventsTab uuid={uuid} status={instance.status} />,
          },
          {
            key: "bills",
            label: t("instances.tabBills"),
            children: <BillsTab instanceId={instance.id} />,
          },
          {
            key: "settings",
            label: t("instances.tabSettings"),
            children: (
              <SettingsTab instance={instance} canRelease={canRelease} onRelease={() => setReleaseOpen(true)} />
            ),
          },
        ]}
      />
      <ReleaseModal
        instance={instance}
        open={releaseOpen}
        onClose={() => setReleaseOpen(false)}
        onReleased={() => void navigate({ to: "/instances", search: listSearch })}
      />
    </Space>
  );
}
