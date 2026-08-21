/** 实例详情:监控(降级文案)/连接/事件时间线(=计费依据)/账单 + 危险区释放。 */

import { isApiError } from "@superdl/api-client";
import { formatDateTime, isTransientInstanceStatus, localToday } from "@superdl/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Radio,
  Space,
  Table,
  Tabs,
  Timeline,
  Typography,
} from "antd";
import { useFormat } from "../lib/format";
import EChart from "../components/EChart";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useResetJupyterToken } from "../api/mutations";
import {
  useDailySummary,
  useHourlyBills,
  useInstance,
  useInstanceAccess,
  useInstanceEvents,
  useInstanceMetrics,
} from "../api/queries";
import { CopyButton, InstanceStatusBadge, TierTag } from "../components/common";
import { InstanceActions, ReleaseModal, canReleaseStatus } from "../components/InstanceActions";
import { DataErrorAlert, moneyOr, TableErrorEmpty } from "../components/QueryState";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/instances_/$uuid")({
  beforeLoad: requireAuth,
  validateSearch: (search: Record<string, unknown>): { tab?: string } => ({
    tab: typeof search["tab"] === "string" ? search["tab"] : undefined,
  }),
  component: InstanceDetail,
});

const SERIES_META = {
  gpu_util: { nameKey: "instances.seriesGpu", unit: "%" },
  vram_used_mb: { nameKey: "instances.seriesVram", unit: "MB" },
  cpu_pct: { nameKey: "instances.seriesCpu", unit: "%" },
  mem_used_mb: { nameKey: "instances.seriesMem", unit: "MB" },
} as const;

function MetricsTab({ uuid, running }: { uuid: string; running: boolean }) {
  const { t } = useTranslation();
  const [range, setRange] = useState<"1h" | "6h" | "24h">("1h");
  const { data, error, isLoading } = useInstanceMetrics(
    uuid,
    { range },
    { enabled: running, refetchInterval: 60_000, retry: 0 },
  );

  if (!running) {
    return <Alert type="info" showIcon title={t("instances.metricsNotRunning")} />;
  }
  if (error && isApiError(error) && error.status === 503) {
    return <Alert type="warning" showIcon title={t("copy.monitoringDown")} />;
  }
  const series = (data?.series ?? {}) as Record<string, [number, number][]>;
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Radio.Group
        value={range}
        onChange={(e) => setRange(e.target.value as typeof range)}
        optionType="button"
        options={[
          { value: "1h", label: t("instances.range1h") },
          { value: "6h", label: t("instances.range6h") },
          { value: "24h", label: t("instances.range24h") },
        ]}
      />
      {Object.entries(SERIES_META).map(([key, meta]) => (
        <Card key={key} size="small" title={t(meta.nameKey)} loading={isLoading}>
          <EChart
            style={{ height: 180 }}
            option={{
              grid: { left: 48, right: 16, top: 16, bottom: 24 },
              xAxis: { type: "time" },
              yAxis: { type: "value", axisLabel: { formatter: `{value}${meta.unit}` } },
              tooltip: { trigger: "axis" },
              series: [
                {
                  type: "line",
                  showSymbol: false,
                  areaStyle: { opacity: 0.08 },
                  data: (series[key] ?? []).map(([ts, v]) => [ts * 1000, v]),
                },
              ],
            }}
          />
        </Card>
      ))}
    </Space>
  );
}

function AccessTab({ uuid, running }: { uuid: string; running: boolean }) {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const { data: access } = useInstanceAccess(uuid, { enabled: running });
  const reset = useResetJupyterToken();
  if (!running) {
    return <Alert type="info" showIcon title={t("instances.accessNotRunning")} />;
  }
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Card size="small" title="SSH">
        <Space orientation="vertical">
          <Typography.Text code>{access?.ssh_command}</Typography.Text>
          {access && <CopyButton text={access.ssh_command} label={t("instances.copyCommand")} />}
          <Typography.Text type="secondary">{t("copy.sshKeyOnly")}</Typography.Text>
        </Space>
      </Card>
      <Card size="small" title="JupyterLab">
        <Space>
          <Button
            type="primary"
            disabled={!access}
            onClick={() => {
              if (access) window.open(access.jupyter_url, "_blank", "noopener,noreferrer");
            }}
          >
            {t("instances.openJupyter")}
          </Button>
          <Button
            onClick={() =>
              modal.confirm({
                title: t("instances.resetTokenConfirmTitle"),
                content: t("instances.resetTokenConfirmBody"),
                onOk: async () => {
                  await reset.mutateAsync(uuid);
                  message.success(t("instances.tokenReset"));
                },
              })
            }
          >
            {t("instances.resetToken")}
          </Button>
        </Space>
      </Card>
    </Space>
  );
}

function EventsTab({ uuid, status }: { uuid: string; status?: string }) {
  const { t } = useTranslation();
  // 时间线即计费依据:过渡态必须跟着状态一起刷新
  const { data: events, isError, refetch } = useInstanceEvents(uuid, {
    refetchInterval: status && isTransientInstanceStatus(status) ? 5_000 : 30_000,
  });
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Alert type="info" showIcon title={t("copy.eventsAreBilling")} />
      {isError && <DataErrorAlert onRetry={() => void refetch()} />}
      <Timeline
        items={(events ?? []).map((e) => ({
          color:
            e.to_status === "running" ? "green" : e.to_status === "failed" ? "red" : "gray",
          content: (
            <Space orientation="vertical" size={0}>
              <Typography.Text strong>
                {e.from_status ?? "—"} → {e.to_status}
                {(e.from_status === "running" || e.to_status === "running") && (
                  <Typography.Text type="secondary">{t("instances.billingBoundary")}</Typography.Text>
                )}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t("instances.eventMetaLine", { time: formatDateTime(e.created_at), reason: e.reason, actor: e.actor })}
              </Typography.Text>
            </Space>
          ),
        }))}
      />
    </Space>
  );
}

function BillsTab({ instanceId }: { instanceId: number }) {
  const { t } = useTranslation();
  const { formatDuration, formatHourlyPrice, formatMoney } = useFormat();
  const { data, isError, refetch } = useHourlyBills({ instance_id: instanceId, limit: 100 });
  return (
    <Table
      rowKey="id"
      size="small"
      pagination={false}
      locale={{
        emptyText: isError ? <TableErrorEmpty onRetry={() => void refetch()} /> : undefined,
      }}
      dataSource={data?.items ?? []}
      columns={[
        { title: t("instances.colBillHour"), render: (_, r) => formatDateTime(r.hour_start) },
        { title: t("instances.colBillDuration"), render: (_, r) => formatDuration(r.seconds_used) },
        {
          title: t("instances.colBillUnit"),
          render: (_, r) => (
            <span>
              {formatHourlyPrice(r.unit_price)} × {r.gpu_count}
            </span>
          ),
        },
        {
          title: t("instances.colBillAmount"),
          render: (_, r) => <span>{formatMoney(r.amount)}</span>,
        },
      ]}
    />
  );
}

function InstanceDetail() {
  const { t } = useTranslation();
  const { formatHourlyPrice, formatMoney } = useFormat();
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
  if (!instance) return null; // 首载中
  const running = instance.status === "running";
  const canRelease = canReleaseStatus(instance.status);

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
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
              <TierTag tier={instance.spec["tier"] as string} />
            </Space>
            <Descriptions
              size="small"
              column={4}
              items={[
                { label: t("instances.labelId"), children: instance.uuid.slice(0, 12) },
                {
                  label: t("instances.labelSpec"),
                  children: t("instances.specLine", { model: instance.spec["gpu_model"] as string, count: instance.gpu_count }),
                },
                {
                  label: t("instances.labelBilling"),
                  children: t("instances.pricePerCard", { price: formatHourlyPrice(instance.price_hourly), count: instance.gpu_count }),
                },
                {
                  label: t("instances.labelToday"),
                  children: moneyOr(formatMoney(todayAmount), daily != null),
                },
                { label: t("instances.createdAt"), children: formatDateTime(instance.created_at) },
              ]}
            />
          </Space>
          <InstanceActions instance={instance} />
        </Space>
      </Card>

      <Tabs
        activeKey={tab ?? "metrics"}
        onChange={(k) =>
          navigate({ to: "/instances/$uuid", params: { uuid }, search: { tab: k } })
        }
        items={[
          {
            key: "metrics",
            label: t("instances.tabMetrics"),
            children: <MetricsTab uuid={uuid} running={running} />,
          },
          {
            key: "access",
            label: t("instances.tabAccess"),
            children: <AccessTab uuid={uuid} running={running} />,
          },
          {
            key: "events",
            label: t("instances.tabEvents"),
            children: <EventsTab uuid={uuid} status={instance?.status} />,
          },
          { key: "bills", label: t("instances.tabBills"), children: <BillsTab instanceId={instance.id} /> },
        ]}
      />

      <Card title={t("instances.dangerZone")} style={{ borderColor: "#ffccc7" }}>
        <Space orientation="vertical">
          <Typography.Text type="secondary">
            {t("instances.dangerNote")}
          </Typography.Text>
          <Button
            danger
            disabled={!canRelease}
            title={canRelease ? undefined : t("copy.releaseNeedsStopped")}
            onClick={() => setReleaseOpen(true)}
          >
            {t("instances.release")}
          </Button>
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
