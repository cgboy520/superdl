/** 实例详情:监控(降级文案)/连接/事件时间线(=计费依据)/账单 + 危险区释放。 */

import { isApiError } from "@superdl/api-client";
import {
  copy,
  formatDateTime,
  formatDuration,
  formatHourlyPrice,
  formatMoney,
  tabularNums,
} from "@superdl/ui";
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
import ReactECharts from "echarts-for-react";
import { useState } from "react";

import { useResetJupyterToken } from "../api/mutations";
import {
  useHourlyBills,
  useInstance,
  useInstanceAccess,
  useInstanceEvents,
  useInstanceMetrics,
} from "../api/queries";
import { CopyButton, InstanceStatusBadge, TierTag } from "../components/common";
import { InstanceActions, ReleaseModal } from "../components/InstanceActions";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/instances/$uuid")({
  beforeLoad: requireAuth,
  validateSearch: (search: Record<string, unknown>): { tab?: string } => ({
    tab: typeof search["tab"] === "string" ? search["tab"] : undefined,
  }),
  component: InstanceDetail,
});

const SERIES_META: Record<string, { name: string; unit: string }> = {
  gpu_util: { name: "GPU 利用率", unit: "%" },
  vram_used_mb: { name: "显存占用", unit: "MB" },
  cpu_pct: { name: "CPU", unit: "%" },
  mem_used_mb: { name: "内存", unit: "MB" },
};

function MetricsTab({ uuid, running }: { uuid: string; running: boolean }) {
  const [range, setRange] = useState<"1h" | "6h" | "24h">("1h");
  const { data, error, isLoading } = useInstanceMetrics(
    uuid,
    { range },
    { enabled: running, refetchInterval: 60_000, retry: 0 },
  );

  if (!running) {
    return <Alert type="info" showIcon message="实例未运行,暂无实时监控" />;
  }
  if (error && isApiError(error) && error.status === 503) {
    return <Alert type="warning" showIcon message={copy.monitoringDown} />;
  }
  const series = (data?.series ?? {}) as Record<string, [number, number][]>;
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Radio.Group
        value={range}
        onChange={(e) => setRange(e.target.value as typeof range)}
        optionType="button"
        options={[
          { value: "1h", label: "1 小时" },
          { value: "6h", label: "6 小时" },
          { value: "24h", label: "24 小时" },
        ]}
      />
      {Object.entries(SERIES_META).map(([key, meta]) => (
        <Card key={key} size="small" title={meta.name} loading={isLoading}>
          <ReactECharts
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
  const { message, modal } = App.useApp();
  const { data: access } = useInstanceAccess(uuid, { enabled: running });
  const reset = useResetJupyterToken();
  if (!running) {
    return <Alert type="info" showIcon message="实例运行中才能获取接入信息" />;
  }
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card size="small" title="SSH">
        <Space direction="vertical">
          <Typography.Text code>{access?.ssh_command}</Typography.Text>
          {access && <CopyButton text={access.ssh_command} label="复制指令" />}
          <Typography.Text type="secondary">{copy.sshKeyOnly}</Typography.Text>
        </Space>
      </Card>
      <Card size="small" title="JupyterLab">
        <Space>
          <Button
            type="primary"
            disabled={!access}
            onClick={() => window.open(access?.jupyter_url, "_blank")}
          >
            打开 JupyterLab
          </Button>
          <Button
            onClick={() =>
              modal.confirm({
                title: "重置 JupyterLab Token?",
                content: "旧链接将失效;运行中的实例会自动重启以生效。",
                onOk: async () => {
                  await reset.mutateAsync(uuid);
                  message.success("Token 已重置");
                },
              })
            }
          >
            重置 Token
          </Button>
        </Space>
      </Card>
    </Space>
  );
}

function EventsTab({ uuid, instanceId }: { uuid: string; instanceId: number }) {
  void instanceId;
  const { data: events } = useInstanceEvents(uuid);
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert type="info" showIcon message={copy.eventsAreBilling} />
      <Timeline
        items={(events ?? []).map((e) => ({
          color:
            e.to_status === "running" ? "green" : e.to_status === "failed" ? "red" : "gray",
          children: (
            <Space direction="vertical" size={0}>
              <Typography.Text strong>
                {e.from_status ?? "—"} → {e.to_status}
                {(e.from_status === "running" || e.to_status === "running") && (
                  <Typography.Text type="secondary">(计费边界)</Typography.Text>
                )}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {formatDateTime(e.created_at)} · {e.reason} · 操作者 {e.actor}
              </Typography.Text>
            </Space>
          ),
        }))}
      />
    </Space>
  );
}

function BillsTab({ instanceId }: { instanceId: number }) {
  const { data } = useHourlyBills({ instance_id: instanceId, limit: 100 });
  return (
    <Table
      rowKey="id"
      size="small"
      pagination={false}
      dataSource={data?.items ?? []}
      columns={[
        { title: "计费小时", render: (_, r) => formatDateTime(r.hour_start) },
        { title: "运行时长", render: (_, r) => formatDuration(r.seconds_used) },
        {
          title: "单价",
          render: (_, r) => (
            <span style={tabularNums}>
              {formatHourlyPrice(r.unit_price)} × {r.gpu_count}
            </span>
          ),
        },
        {
          title: "金额",
          render: (_, r) => <span style={tabularNums}>{formatMoney(r.amount)}</span>,
        },
      ]}
    />
  );
}

function InstanceDetail() {
  const { uuid } = Route.useParams();
  const { tab } = Route.useSearch();
  const navigate = useNavigate();
  const [releaseOpen, setReleaseOpen] = useState(false);
  const { data: instance } = useInstance(uuid, { refetchInterval: 5_000 });

  if (!instance) return null;
  const running = instance.status === "running";
  const canRelease = ["stopped", "frozen", "failed"].includes(instance.status);

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
          <Space direction="vertical" size={4}>
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
                { label: "ID", children: instance.uuid.slice(0, 12) },
                {
                  label: "规格",
                  children: `${instance.spec["gpu_model"] as string} × ${instance.gpu_count}`,
                },
                {
                  label: "计费",
                  children: `${formatHourlyPrice(instance.price_hourly)} × ${instance.gpu_count} 卡`,
                },
                { label: "创建于", children: formatDateTime(instance.created_at) },
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
            label: "监控",
            children: <MetricsTab uuid={uuid} running={running} />,
          },
          {
            key: "access",
            label: "连接",
            children: <AccessTab uuid={uuid} running={running} />,
          },
          {
            key: "events",
            label: "事件",
            children: <EventsTab uuid={uuid} instanceId={instance.id} />,
          },
          { key: "bills", label: "账单", children: <BillsTab instanceId={instance.id} /> },
        ]}
      />

      <Card title="危险区" style={{ borderColor: "#ffccc7" }}>
        <Space direction="vertical">
          <Typography.Text type="secondary">
            释放实例将清除实例盘全部数据(数据盘不受影响),不可恢复。
          </Typography.Text>
          <Button
            danger
            disabled={!canRelease}
            title={canRelease ? undefined : copy.releaseNeedsStopped}
            onClick={() => setReleaseOpen(true)}
          >
            释放实例
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
