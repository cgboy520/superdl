/**
 * 容器实例列表(默认落地页):策略提示条 + 动作行(租用/刷新/密钥设置/搜索)+ 7 列密集表格
 * (名称/状态/规格/GPU利用率 sparkline/计费+今日消费/快捷工具/操作)。
 * 实例 5s 轮询;指标摘要 45s、今日消费 60s 独立轮询(互不拖累)。
 */

import { CodeOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { type InstanceMetricsSummaryOut, type InstanceOut } from "@superdl/api-client";
import { copy, formatDateTime, formatHourlyPrice, formatMoney, localToday } from "@superdl/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Empty,
  Input,
  Popover,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useMemo, useState } from "react";

import { useRenameInstance } from "../api/mutations";
import { useDailySummary, useInstanceAccess, useInstances, useMetricsSummary } from "../api/queries";
import { CopyButton, InstanceStatusBadge, TierTag } from "../components/common";
import { TableErrorEmpty } from "../components/QueryState";
import { GpuSparkline } from "../components/GpuSparkline";
import { InstanceActions } from "../components/InstanceActions";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/instances")({
  beforeLoad: requireAuth,
  component: InstancesPage,
});

function QuickToolsCell({ instance }: { instance: InstanceOut }) {
  const navigate = useNavigate();
  const running = instance.status === "running";
  const { data: access } = useInstanceAccess(instance.uuid, { enabled: running });
  const monitorLink = (
    <Button
      size="small"
      type="link"
      style={{ paddingInline: 0, height: 22 }}
      onClick={() =>
        navigate({
          to: "/instances/$uuid",
          params: { uuid: instance.uuid },
          search: { tab: "metrics" },
        })
      }
    >
      实例监控
    </Button>
  );
  if (!running) {
    return (
      <Space orientation="vertical" size={0}>
        <Tooltip title={copy.jupyterNeedsRunning}>
          <Space size={4}>
            <Button size="small" disabled icon={<CodeOutlined />}>
              SSH
            </Button>
            <Button size="small" disabled>
              JupyterLab
            </Button>
          </Space>
        </Tooltip>
        {monitorLink}
      </Space>
    );
  }
  return (
    <Space orientation="vertical" size={0}>
      <Space size={4}>
        {access ? <CopyButton text={access.ssh_command} label="SSH" /> : null}
        <Button
          size="small"
          type="link"
          disabled={!access}
          style={{ paddingInline: 4 }}
          onClick={() => window.open(access?.jupyter_url, "_blank")}
        >
          JupyterLab
        </Button>
      </Space>
      {monitorLink}
    </Space>
  );
}

function UtilCell({
  instance,
  summary,
}: {
  instance: InstanceOut;
  summary: InstanceMetricsSummaryOut | undefined;
}) {
  if (instance.status !== "running") {
    return <Typography.Text type="secondary">-</Typography.Text>;
  }
  if (summary && !summary.available) {
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {copy.metricsUnavailableShort}
      </Typography.Text>
    );
  }
  const item = summary?.items.find((i) => i.uuid === instance.uuid);
  if (!item || item.points.length === 0) {
    return <Typography.Text type="secondary">-</Typography.Text>;
  }
  return (
    <Space size={8} align="center">
      <GpuSparkline points={item.points} />
      <span style={{ fontSize: 12 }}>{Math.round(item.last ?? 0)}%</span>
    </Space>
  );
}

function NameCell({ instance, onDetail }: { instance: InstanceOut; onDetail: () => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(instance.name);
  const rename = useRenameInstance();
  const { message } = App.useApp();
  if (editing) {
    return (
      <Input
        size="small"
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => setEditing(false)}
        onPressEnter={async () => {
          await rename.mutateAsync({ uuid: instance.uuid, name: value });
          setEditing(false);
          message.success("已改名");
        }}
        style={{ width: 160 }}
      />
    );
  }
  return (
    <Space orientation="vertical" size={0}>
      <Tooltip title="点击改名">
        <Typography.Text strong style={{ cursor: "pointer" }} onClick={() => setEditing(true)}>
          {instance.name}
        </Typography.Text>
      </Tooltip>
      <Space size={8}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {instance.uuid.slice(0, 12)}
        </Typography.Text>
        <Button size="small" type="link" style={{ paddingInline: 0, height: 20, fontSize: 12 }} onClick={onDetail}>
          详情
        </Button>
      </Space>
    </Space>
  );
}

function InstancesPage() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const { data: instances, isLoading, isError, refetch } = useInstances({ refetchInterval: 5_000 });
  const { data: metrics } = useMetricsSummary({ refetchInterval: 45_000 });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: 60_000 });

  const todayByInstance = useMemo(
    () => new Map((daily?.items ?? []).map((it) => [it.instance_id, it.total_amount])),
    [daily],
  );

  const rows = (instances ?? []).filter(
    (i) => !q || i.name.includes(q) || i.uuid.includes(q.toLowerCase()),
  );

  const openDetail = (uuid: string, tab?: string) =>
    navigate({
      to: "/instances/$uuid",
      params: { uuid },
      search: tab ? { tab } : undefined,
    });

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        容器实例
      </Typography.Title>
      <Alert type="info" showIcon title={copy.freezePolicy} />
      <Space style={{ width: "100%", justifyContent: "space-between" }} wrap>
        <Space size={8}>
          <Link to="/market">
            <Button type="primary">租用新实例</Button>
          </Link>
          <Tooltip title="刷新列表">
            <Button icon={<ReloadOutlined />} onClick={() => void refetch()} />
          </Tooltip>
        </Space>
        <Space size={12}>
          <Link to="/settings">密钥登录设置</Link>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索名称 / ID"
            style={{ width: 220 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </Space>
      </Space>
      <Table<InstanceOut>
        rowKey="uuid"
        loading={isLoading}
        dataSource={rows}
        pagination={false}
        scroll={{ x: 960 }}
        locale={{
          emptyText: isError ? (
            <TableErrorEmpty onRetry={() => void refetch()} />
          ) : q ? (
            "没有匹配的实例"
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Space orientation="vertical" size={4}>
                  <Typography.Text strong>还没有实例</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    按量计费,关机不收 GPU 费用;数据盘独立保留
                  </Typography.Text>
                </Space>
              }
            >
              <Link to="/market">
                <Button type="primary">去算力市场</Button>
              </Link>
            </Empty>
          ),
        }}
        columns={[
          {
            title: "名称 / ID",
            render: (_, r) => <NameCell instance={r} onDetail={() => void openDetail(r.uuid)} />,
          },
          {
            title: "状态",
            render: (_, r) => (
              <Tooltip title={r.status === "stopped" ? copy.freezePolicy : undefined}>
                <span>
                  <InstanceStatusBadge status={r.status} frozenDeadline={r.frozen_deadline} />
                </span>
              </Tooltip>
            ),
          },
          {
            title: "规格详情",
            render: (_, r) => (
              <Popover
                content={
                  <Space orientation="vertical" size={2}>
                    <span>{r.spec["sku_name"] as string}</span>
                    <span>
                      {r.spec["vcpu"] as number} vCPU / {r.spec["mem_gb"] as number}G 内存 /
                      实例盘 {r.spec["disk_gb"] as number}G
                    </span>
                    <span>镜像:{r.image_ref}</span>
                    <span>创建于 {formatDateTime(r.created_at)}</span>
                  </Space>
                }
              >
                <Space>
                  <span>
                    {r.spec["gpu_model"] as string} × {r.gpu_count}
                  </span>
                  <TierTag tier={r.spec["tier"] as string} />
                </Space>
              </Popover>
            ),
          },
          {
            title: "GPU 利用率",
            render: (_, r) => <UtilCell instance={r} summary={metrics} />,
          },
          {
            title: "计费",
            render: (_, r) => (
              <Space orientation="vertical" size={0}>
                <Space size={6}>
                  <Tag style={{ marginInlineEnd: 0 }}>按量</Tag>
                  <span>
                    {formatHourlyPrice(r.price_hourly)} × {r.gpu_count} 卡
                  </span>
                </Space>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  今日 {formatMoney(todayByInstance.get(r.id) ?? null)}
                </Typography.Text>
              </Space>
            ),
          },
          { title: "快捷工具", render: (_, r) => <QuickToolsCell instance={r} /> },
          {
            title: "操作",
            fixed: "right",
            render: (_, r) => (
              <InstanceActions
                instance={r}
                onShowEvents={() => void openDetail(r.uuid, "events")}
              />
            ),
          },
        ]}
        onRow={(r) => ({
          onDoubleClick: () => void openDetail(r.uuid),
        })}
      />
    </Space>
  );
}
