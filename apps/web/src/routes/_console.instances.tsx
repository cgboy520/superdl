/**
 * 容器实例列表(默认落地页):策略提示条 + 动作行(租用/刷新/密钥设置/状态过滤/搜索)+ 密集表格
 * (名称/状态/规格/GPU利用率 sparkline/计费+今日消费/操作)。
 * 列表走服务端游标分页 + status/name 过滤(状态入 URL);
 * access/events 在行展开时按需加载,不在行内预取;轮询只回刷第一页(摘要列),旧页不重取。
 */

import { CodeOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { type InstanceMetricsSummaryOut, type InstanceOut } from "@superdl/api-client";
import {
  formatDateTime,
  instanceStatusMap,
  localToday,
  metaOf,
} from "@superdl/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Empty,
  Input,
  Popover,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useDeferredValue, useEffect, useMemo, useState, memo } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "../lib/format";
import { useRenameInstance } from "../api/mutations";
import {
  useDailySummary,
  useInstanceAccess,
  useInstanceEvents,
  useInstancePages,
  useMetricsSummary,
  usePolicies,
} from "../api/queries";
import { CopyButton, InstanceStatusBadge, TierTag } from "../components/common";
import { TableErrorEmpty } from "../components/QueryState";
import { GpuSparkline } from "../components/GpuSparkline";
import { InstanceActions } from "../components/InstanceActions";
import { requireAuth } from "../lib/guard";

/** 可过滤的状态(released 终态不出列表,过滤项同步不给) */
const FILTER_STATUSES = [
  "creating",
  "running",
  "stopping",
  "stopped",
  "starting",
  "frozen",
  "releasing",
  "failed",
] as const;

export const Route = createFileRoute("/_console/instances")({
  beforeLoad: requireAuth,
  // 列表状态入 URL:可分享/返回不丢;非法值丢弃回默认
  validateSearch: (search: Record<string, unknown>): { q?: string; status?: string } => {
    const out: { q?: string; status?: string } = {};
    if (typeof search.q === "string" && search.q.trim()) out.q = search.q;
    if (
      typeof search.status === "string" &&
      (FILTER_STATUSES as readonly string[]).includes(search.status)
    ) {
      out.status = search.status;
    }
    return out;
  },
  component: InstancesPage,
});

/** 展开行(running):SSH/Jupyter 快捷工具,access 仅在展开时拉取。 */
function ExpandedTools({ instance }: { instance: InstanceOut }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data: access, isError } = useInstanceAccess(instance.uuid);
  return (
    <Space size={12} wrap align="center">
      {isError ? (
        <Typography.Text type="secondary">{t("query.loadFailed")}</Typography.Text>
      ) : access ? (
        <CopyButton text={access.ssh_command} label="SSH" />
      ) : null}
      <Button
        size="small"
        icon={<CodeOutlined />}
        disabled={!access}
        onClick={() => {
          if (access) window.open(access.jupyter_url, "_blank", "noopener,noreferrer");
        }}
      >
        {t("common.jupyter")}
      </Button>
      <Button
        size="small"
        type="link"
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
      {!access && !isError && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t("copy.jupyterNeedsRunning")}
        </Typography.Text>
      )}
    </Space>
  );
}

/** 展开行(failed):事件按需加载,给失败原因 + 未扣费/重新创建闭环。 */
function ExpandedFailed({ instance }: { instance: InstanceOut }) {
  const { t } = useTranslation();
  const { data: events, isError } = useInstanceEvents(instance.uuid);
  if (isError) {
    return <Typography.Text type="secondary">{t("query.loadFailed")}</Typography.Text>;
  }
  // 服务端降序(最新在前):最新一次 failed 原因取首元素
  const failedEvents = (events?.items ?? []).filter((e) => e.to_status === "failed");
  const reason = failedEvents[0]?.reason;
  const everRan = (events?.items ?? []).some((e) => e.to_status === "running");
  return (
    <Space orientation="vertical" size={4}>
      {reason ? (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
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

// 轮询行 memo:react-query 结构共享保证数据未变时引用不变,
// 5s/30s 轮询只重渲真正变化的行;onDetail 每次渲染都新建,但从 uuid 派生,比较时排除
const UtilCellMemo = memo(
  UtilCell,
  (prev, next) =>
    prev.instance.uuid === next.instance.uuid &&
    prev.instance.status === next.instance.status &&
    prev.summary?.items.find((i) => i.uuid === prev.instance.uuid) ===
      next.summary?.items.find((i) => i.uuid === prev.instance.uuid),
);

const NameCellMemo = memo(
  NameCell,
  (prev, next) => prev.instance.uuid === next.instance.uuid && prev.instance.name === next.instance.name,
);

function InstancesPage() {
  const { t } = useTranslation(["web", "shared"]);
  const { formatHourlyPrice, formatMoney } = useFormat();
  const navigate = useNavigate();
  const { q, status } = Route.useSearch();
  const [keyword, setKeyword] = useState(q ?? "");
  // 搜索输入防抖走 useDeferredValue:击键不直接打服务端/写 URL
  const deferredKeyword = useDeferredValue(keyword);
  const deferredQ = deferredKeyword.trim();
  const { data: policies } = usePolicies();
  const pagesQ = useInstancePages({
    status,
    name: deferredQ || undefined,
  });
  const { data, isLoading, isError, refetch, hasNextPage, isFetchingNextPage, fetchNextPage } =
    pagesQ;
  const { data: metrics } = useMetricsSummary({ refetchInterval: 45_000 });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: 60_000 });

  const todayByInstance = useMemo(
    () => new Map((daily?.items ?? []).map((it) => [it.instance_id, it.total_amount])),
    [daily],
  );

  // 首页轮询替换 + 已加载旧页之间可能短暂重叠:按 uuid 去重(首页新数据优先)
  const rows = useMemo<InstanceOut[]>(() => {
    const seen = new Set<string>();
    const out: InstanceOut[] = [];
    for (const p of data?.pages ?? []) {
      for (const i of p.items) {
        if (seen.has(i.uuid)) continue;
        seen.add(i.uuid);
        out.push(i);
      }
    }
    return out;
  }, [data]);

  const setSearch = (patch: { q?: string; status?: string }) =>
    void navigate({
      to: "/instances",
      search: (prev: { q?: string; status?: string }) => {
        const merged = { ...prev, ...patch };
        return {
          q: merged.q || undefined,
          status: merged.status || undefined,
        };
      },
      replace: true,
    });

  // 列表状态入 URL(replace,不产生历史垃圾):可分享、详情返回不丢
  useEffect(() => {
    if ((q ?? "") === deferredQ) return;
    void navigate({
      to: "/instances",
      search: { q: deferredQ || undefined, status },
      replace: true,
    });
  }, [q, deferredQ, status, navigate]);

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
      <Alert
        type="info"
        showIcon
        title={
          policies
            ? t("copy.freezePolicy", { hours: policies.freeze_grace_hours })
            : t("copy.freezePolicyFallback")
        }
      />
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
          <Select
            allowClear
            style={{ width: 140 }}
            placeholder={t("instances.statusFilter")}
            aria-label={t("instances.statusFilter")}
            value={status ?? null}
            onChange={(v: string | null) => setSearch({ status: v ?? undefined })}
            options={FILTER_STATUSES.map((s) => {
              const meta = metaOf(instanceStatusMap, s);
              // 裸状态码不进 t():extract 会把它当成新键收集
              return { value: s, label: meta ? t(meta.labelKey) : s };
            })}
          />
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder={t("instances.searchPlaceholder")}
            aria-label={t("instances.searchPlaceholder")}
            style={{ width: 220 }}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
        </Space>
      </Space>
      <Table<InstanceOut>
        rowKey="uuid"
        loading={isLoading}
        dataSource={rows}
        pagination={false}
        scroll={{ x: 960 }}
        expandable={{
          // access/events 行展开按需加载:仅 running(工具)/failed(原因)可展开
          rowExpandable: (r) => r.status === "running" || r.status === "failed",
          expandedRowRender: (r) =>
            r.status === "failed" ? <ExpandedFailed instance={r} /> : <ExpandedTools instance={r} />,
        }}
        locale={{
          emptyText: isError ? (
            <TableErrorEmpty onRetry={() => void refetch()} />
          ) : keyword || status ? (
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
            render: (_, r) => <NameCellMemo instance={r} onDetail={() => void openDetail(r.uuid)} />,
          },
          {
            title: t("instances.colStatus"),
            render: (_, r) => (
              <Tooltip
                title={
                  r.status === "stopped"
                    ? policies
                      ? t("copy.freezePolicy", { hours: policies.freeze_grace_hours })
                      : t("copy.freezePolicyFallback")
                    : undefined
                }
              >
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
                        vcpu: (r.spec["vcpu"] as number) * r.gpu_count,
                        mem: (r.spec["mem_gb"] as number) * r.gpu_count,
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
            render: (_, r) => <UtilCellMemo instance={r} summary={metrics} />,
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
      {hasNextPage && (
        <Button block loading={isFetchingNextPage} onClick={() => void fetchNextPage()}>
          {t("billing.loadMore")}
        </Button>
      )}
    </Space>
  );
}
