/**
 * 容器实例列表(默认落地页):策略提示条 + 动作行(租用/刷新/密钥设置/搜索)+ 7 列密集表格
 * (名称/状态/规格/GPU利用率 sparkline/计费+今日消费/快捷工具/操作)。
 * 实例 5s 轮询;指标摘要 45s、今日消费 60s 独立轮询(互不拖累)。
 */

import { CodeOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { type InstanceMetricsSummaryOut, type InstanceOut } from "@superdl/api-client";
import { formatDateTime, isTransientInstanceStatus, localToday } from "@superdl/ui";
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
import { useTranslation } from "react-i18next";

import { useFormat } from "../lib/format";
import { useRenameInstance } from "../api/mutations";
import {
  useDailySummary,
  useInstanceAccess,
  useInstanceEvents,
  useInstances,
  useMetricsSummary,
} from "../api/queries";
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
  const { t } = useTranslation();
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
      {t("instances.monitorLink")}
    </Button>
  );
  if (!running) {
    return (
      <Space orientation="vertical" size={0}>
        <Tooltip title={t("copy.jupyterNeedsRunning")}>
          <Space size={4}>
            <Button size="small" disabled icon={<CodeOutlined />}>
              {t("common.ssh")}
            </Button>
            <Button size="small" disabled>
              {t("common.jupyter")}
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
          onClick={() => {
                if (access) window.open(access.jupyter_url, "_blank", "noopener,noreferrer");
              }}
        >
          {t("common.jupyter")}
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
  const { t } = useTranslation();
  if (instance.status !== "running") {
    return <Typography.Text type="secondary">-</Typography.Text>;
  }
  if (summary && !summary.available) {
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {t("copy.metricsUnavailableShort")}
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

/**
 * failed 状态闭环:从未运行 = 创建失败 → 原因 + 未扣费说明 + 重新创建;
 * 运行过 = 故障停机 → 按停机结算说明。
 */
function FailedCell({ instance }: { instance: InstanceOut }) {
  const { t } = useTranslation();
  const { data: events } = useInstanceEvents(instance.uuid);
  // 服务端降序(最新在前):最新一次 failed 原因取首元素
  const failedEvents = (events?.items ?? []).filter((e) => e.to_status === "failed");
  const reason = failedEvents[0]?.reason;
  const everRan = (events?.items ?? []).some((e) => e.to_status === "running");
  return (
    <Space orientation="vertical" size={4}>
      <InstanceStatusBadge status={instance.status} frozenDeadline={instance.frozen_deadline} />
      {reason ? (
        <Typography.Text
          type="secondary"
          style={{ fontSize: 12, maxWidth: 200, display: "inline-block" }}
          ellipsis={{ tooltip: reason }}
        >
          {reason}
        </Typography.Text>
      ) : null}
      {everRan ? (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t("instances.failedRanNote")}
        </Typography.Text>
      ) : (
        <Space size={6}>
          <Tooltip title={t("copy.createFailedNoCharge")}>
            <Tag color="green" style={{ marginInlineEnd: 0 }}>
              {t("instances.notCharged")}
            </Tag>
          </Tooltip>
          <Link to="/market/create/$skuId" params={{ skuId: String(instance.sku_id) }}>
            <Button size="small" type="primary">
              {t("instances.recreate")}
            </Button>
          </Link>
        </Space>
      )}
    </Space>
  );
}

function NameCell({ instance, onDetail }: { instance: InstanceOut; onDetail: () => void }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(instance.name);
  const rename = useRenameInstance();
  const { message } = App.useApp();
  // 失焦即保存(仅在有改动时)
  const save = async () => {
    if (rename.isPending) return;
    const name = value.trim();
    if (!name || name === instance.name) {
      setValue(instance.name);
      setEditing(false);
      return;
    }
    try {
      await rename.mutateAsync({ uuid: instance.uuid, name });
      message.success(t("instances.renamed"));
      setEditing(false);
    } catch {
      // 错误提示由 useApiMutation 统一弹出;保持编辑态不丢输入
    }
  };
  if (editing) {
    return (
      <Input
        size="small"
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => void save()}
        onPressEnter={() => void save()}
        style={{ width: 160 }}
      />
    );
  }
  return (
    <Space orientation="vertical" size={0}>
      <Typography.Text
        strong
        style={{ cursor: "pointer" }}
        role="button"
        tabIndex={0}
        aria-label={t("instances.renameAria", { name: instance.name })}
        onClick={() => {
          setValue(instance.name);
          setEditing(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setValue(instance.name);
            setEditing(true);
          }
        }}
      >
        {instance.name}
      </Typography.Text>
      <Space size={8}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {instance.uuid.slice(0, 12)}
        </Typography.Text>
        <Button size="small" type="link" style={{ paddingInline: 0, height: 20, fontSize: 12 }} onClick={onDetail}>
          {t("instances.detail")}
        </Button>
      </Space>
    </Space>
  );
}

function InstancesPage() {
  const { t } = useTranslation();
  const { formatHourlyPrice, formatMoney } = useFormat();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const {
    data: instances,
    isLoading,
    isError,
    refetch,
  } = useInstances({
    refetchInterval: (q) =>
      (q.state.data ?? []).some((i) => isTransientInstanceStatus(i.status)) ? 5_000 : 30_000,
  });
  const { data: metrics } = useMetricsSummary({ refetchInterval: 45_000 });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: 60_000 });

  const todayByInstance = useMemo(
    () => new Map((daily?.items ?? []).map((it) => [it.instance_id, it.total_amount])),
    [daily],
  );

  // 名称与 uuid 统一按小写比对,同一个搜索框不能有两套大小写口径
  const rows = (instances ?? []).filter((i) => {
    const needle = q.trim().toLowerCase();
    return !needle || i.name.toLowerCase().includes(needle) || i.uuid.includes(needle);
  });

  const openDetail = (uuid: string, tab?: string) =>
    navigate({
      to: "/instances/$uuid",
      params: { uuid },
      search: tab ? { tab } : undefined,
    });

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("instances.title")}
      </Typography.Title>
      <Alert type="info" showIcon title={t("copy.freezePolicy")} />
      <Space style={{ width: "100%", justifyContent: "space-between" }} wrap>
        <Space size={8}>
          <Link to="/market">
            <Button type="primary">{t("instances.rentNew")}</Button>
          </Link>
          <Button
            aria-label={t("instances.refreshList")}
            icon={<ReloadOutlined />}
            onClick={() => void refetch()}
          />
        </Space>
        <Space size={12}>
          <Link to="/settings" hash="ssh">
            {t("instances.keySettings")}
          </Link>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder={t("instances.searchPlaceholder")}
            aria-label={t("instances.searchPlaceholder")}
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
            t("instances.noMatch")
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Space orientation="vertical" size={4}>
                  <Typography.Text strong>{t("instances.emptyTitle")}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {t("instances.emptyHint")}
                  </Typography.Text>
                </Space>
              }
            >
              <Link to="/market">
                <Button type="primary">{t("instances.goMarket")}</Button>
              </Link>
            </Empty>
          ),
        }}
        columns={[
          {
            title: t("instances.colName"),
            render: (_, r) => <NameCell instance={r} onDetail={() => void openDetail(r.uuid)} />,
          },
          {
            title: t("instances.colStatus"),
            render: (_, r) =>
              r.status === "failed" ? (
                <FailedCell instance={r} />
              ) : (
                <Tooltip title={r.status === "stopped" ? t("copy.freezePolicy") : undefined}>
                  <span>
                    <InstanceStatusBadge status={r.status} frozenDeadline={r.frozen_deadline} />
                  </span>
                </Tooltip>
              ),
          },
          {
            title: t("instances.colSpec"),
            render: (_, r) => (
              <Popover
                content={
                  <Space orientation="vertical" size={2}>
                    <span>{r.spec["sku_name"] as string}</span>
                    <span>
                      {t("common.hostSpec", {
                        vcpu: r.spec["vcpu"] as number,
                        mem: r.spec["mem_gb"] as number,
                        disk: r.spec["disk_gb"] as number,
                      })}
                    </span>
                    <span>{t("instances.imageLine", { ref: r.image_ref })}</span>
                    <span>{t("instances.createdAtLine", { time: formatDateTime(r.created_at) })}</span>
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
            title: t("instances.colUtil"),
            render: (_, r) => <UtilCell instance={r} summary={metrics} />,
          },
          {
            title: t("instances.colBilling"),
            render: (_, r) => (
              <Space orientation="vertical" size={0}>
                <Space size={6}>
                  <Tag style={{ marginInlineEnd: 0 }}>{t("instances.payAsYouGo")}</Tag>
                  <span>
                    {t("instances.pricePerCard", { price: formatHourlyPrice(r.price_hourly), count: r.gpu_count })}
                  </span>
                </Space>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {t("instances.todayCost", { amount: formatMoney(todayByInstance.get(r.id)) })}
                </Typography.Text>
              </Space>
            ),
          },
          { title: t("instances.colTools"), render: (_, r) => <QuickToolsCell instance={r} /> },
          {
            title: t("instances.colActions"),
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
