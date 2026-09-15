/** 实例列表:URL 筛选、游标分页、过渡态轮询、注意事项与空态引导。 */

import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { type InstanceMetricsSummaryOut, type InstanceOut } from "@superdl/api-client";
import {
  controlWidth,
  fontSize,
  formatDateTime,
  isSubscriptionExpired,
  layout,
  localToday,
  POLL,
  space,
  useAutoRefresh,
} from "@superdl/ui";
import {
  AttentionBar,
  EmptyState,
  FilterBar,
  LoadMore,
  PageContainer,
  StatusSummaryBar,
  TableErrorEmpty,
} from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Button, Card, Grid, Input, Popover, Skeleton, Space, Table, Tooltip, Typography } from "antd";
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useDailySummary,
  useInstancePages,
  useMetricsSummary,
  usePolicies,
  useTransientInstanceRefresh,
} from "../api/queries";
import { useNotificationAttention } from "../components/useNotificationAttention";
import { BillingCell } from "../components/BillingCell";
import { InstanceStatusBadge, TierTag } from "../components/common";
import { RenewModal } from "../components/RenewModal";
import { GpuSparkline } from "../components/GpuSparkline";
import { InstanceActions } from "../components/InstanceActions";
import { useInstanceAttention } from "../components/InstanceAttention";
import { InstanceNameCellMemo } from "../components/InstanceNameCell";
import { OnboardingSteps } from "../components/OnboardingSteps";
import { requireAuth } from "../lib/guard";
import { useCursorList } from "../lib/useCursorList";
import { listSearchStore } from "../stores/listSearch";

/** 状态计数条的视图键 = ?status= 白名单;running / stopped 直落服务端过滤,attention 是客户端派生集合。 */
const SUMMARY_KEYS = ["running", "stopped", "attention"] as const;

/** 需处理状态集合:过渡态、冻结与失败。 */
const ATTENTION_STATUSES = new Set(["creating", "starting", "stopping", "frozen", "failed"]);

/** 需处理判据(与 §3.2 一致):上表状态,或包周期已到期 / 在 period_expire_warn_days 窗口内到期。 */
function needsAttention(i: InstanceOut, warnDays: number | undefined, now: number): boolean {
  if (ATTENTION_STATUSES.has(i.status)) return true;
  const sub = i.subscription;
  if (!sub) return false;
  if (isSubscriptionExpired(i.market, sub, new Date(now))) return true;
  return warnDays !== undefined && new Date(sub.expires_at).getTime() - now <= warnDays * 86_400_000;
}

export const Route = createFileRoute("/_console/instances")({
  beforeLoad: requireAuth,
  validateSearch: (search: Record<string, unknown>): { q?: string; status?: string } => {
    const out: { q?: string; status?: string } = {};
    if (typeof search.q === "string" && search.q.trim()) out.q = search.q;
    if (typeof search.status === "string" && (SUMMARY_KEYS as readonly string[]).includes(search.status)) {
      out.status = search.status;
    }
    return out;
  },
  component: InstancesPage,
});

function UtilCell({ instance, summary }: { instance: InstanceOut; summary: InstanceMetricsSummaryOut | undefined }) {
  const { t } = useTranslation();
  if (instance.status !== "running") {
    return <Typography.Text type="secondary">-</Typography.Text>;
  }
  if (summary && !summary.available) {
    return (
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {t("copy.metricsUnavailableShort")}
      </Typography.Text>
    );
  }
  const item = summary?.items.find((i) => i.uuid === instance.uuid);
  if (!item || item.points.length === 0) {
    return <Typography.Text type="secondary">-</Typography.Text>;
  }
  return (
    <Space size={space.sm} align="center">
      <GpuSparkline points={item.points} />
      <span style={{ fontSize: fontSize.caption }}>{Math.round(item.last ?? 0)}%</span>
    </Space>
  );
}

/** 状态列(表格与移动卡片共用):stopped 时 tooltip 给冻结策略(政策说明不做常驻条)。 */
function StatusCell({ instance, freezeGraceHours }: { instance: InstanceOut; freezeGraceHours?: number }) {
  const { t } = useTranslation(["web", "shared"]);
  return (
    <Tooltip
      title={
        instance.status === "stopped"
          ? freezeGraceHours !== undefined
            ? t("copy.freezePolicy", { hours: freezeGraceHours })
            : t("copy.freezePolicyFallback")
          : undefined
      }
    >
      <span>
        <InstanceStatusBadge status={instance.status} frozenDeadline={instance.frozen_deadline} />
      </span>
    </Tooltip>
  );
}

/** 规格列:GPU 型号 × 数量 + 档位徽标,popover 展开完整配置(hover / focus / click 三触发,触屏可达)。 */
function SpecCell({ instance }: { instance: InstanceOut }) {
  const { t } = useTranslation(["web", "shared"]);
  return (
    <Popover
      trigger={["hover", "focus", "click"]}
      content={
        <Space orientation="vertical" size={space.xs}>
          <span>{instance.spec.sku_name as string}</span>
          <span>
            {t("common.hostSpec", {
              vcpu: (instance.spec.vcpu as number) * instance.gpu_count,
              mem: (instance.spec.mem_gb as number) * instance.gpu_count,
              disk: instance.spec.disk_gb as number,
            })}
          </span>
          <span style={{ maxWidth: 360, wordBreak: "break-all" }}>
            {t("instances.imageLine", { ref: instance.image_ref })}
          </span>
          <span>{t("instances.createdAtLine", { time: formatDateTime(instance.created_at) })}</span>
        </Space>
      }
    >
      <Space>
        <Typography.Text
          tabIndex={0}
          className="focus-ring"
          style={{ textDecoration: "underline dotted", cursor: "help" }}
        >
          {instance.spec.gpu_model as string} × {instance.gpu_count}
        </Typography.Text>
        <TierTag tier={instance.spec.tier as string} pool={instance.spec.pool_label as string} />
      </Space>
    </Popover>
  );
}

const UtilCellMemo = memo(
  UtilCell,
  (prev, next) =>
    prev.instance.uuid === next.instance.uuid &&
    prev.instance.status === next.instance.status &&
    prev.summary?.items.find((i) => i.uuid === prev.instance.uuid) ===
      next.summary?.items.find((i) => i.uuid === prev.instance.uuid),
);

/** 移动端实例卡片(<md 替代表格):与表格共用名称/状态/规格/利用率/计费单元。 */
function InstanceCard({
  instance,
  summary,
  todayByInstance,
  dailyReady,
  freezeGraceHours,
  onShowEvents,
}: {
  instance: InstanceOut;
  summary: InstanceMetricsSummaryOut | undefined;
  todayByInstance: ReadonlyMap<number, string>;
  dailyReady: boolean;
  freezeGraceHours?: number;
  onShowEvents: () => void;
}) {
  return (
    <Card size="small">
      <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
        <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
          <InstanceNameCellMemo instance={instance} />
          <StatusCell instance={instance} freezeGraceHours={freezeGraceHours} />
        </Space>
        <SpecCell instance={instance} />
        <Space size={space.lg} wrap align="start">
          <UtilCellMemo instance={instance} summary={summary} />
          <BillingCell instance={instance} todayByInstance={todayByInstance} dailyReady={dailyReady} />
        </Space>
        <InstanceActions instance={instance} onShowEvents={onShowEvents} />
      </Space>
    </Card>
  );
}

function InstancesPage() {
  const { t } = useTranslation(["web", "shared"]);
  const navigate = useNavigate();
  const narrow = !Grid.useBreakpoint().md;
  const [renewTarget, setRenewTarget] = useState<InstanceOut | null>(null);
  const { q, status } = Route.useSearch();
  const { data: policies } = usePolicies();
  const commitQ = useCallback(
    (next: string | undefined) => void navigate({ to: "/instances", search: { q: next, status }, replace: true }),
    [navigate, status],
  );
  const {
    keyword,
    setKeyword,
    rows,
    isLoading,
    isError,
    refetch,
    isRefetching,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
    fetchNextPage,
  } = useCursorList({
    urlQ: q,
    commitQ,
    usePages: (name) => useInstancePages({ status: status === "attention" ? undefined : status, name }),
    keyOf: (i: InstanceOut) => i.uuid,
  });
  const auto = useAutoRefresh(POLL.metrics);
  const metricsQ = useMetricsSummary({ refetchInterval: auto.refetchInterval });
  const { data: metrics } = metricsQ;
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, {
    refetchInterval: auto.paused ? false : POLL.daily,
  });

  const todayByInstance = useMemo(
    () => new Map((daily?.items ?? []).map((it) => [it.instance_id, it.total_amount])),
    [daily],
  );

  useTransientInstanceRefresh(rows);

  const warnDays = policies?.period_expire_warn_days;
  const [mountedAt] = useState(() => Date.now());
  const attentionRows = useMemo(
    () => rows.filter((r) => needsAttention(r, warnDays, mountedAt)),
    [rows, warnDays, mountedAt],
  );
  const visibleRows = status === "attention" ? attentionRows : rows;

  const attention = [...useNotificationAttention(), ...useInstanceAttention(rows, setRenewTarget)];

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

  useEffect(() => {
    listSearchStore.getState().remember("/instances", { q, status });
  }, [q, status]);

  const openDetail = (uuid: string, tab?: string) =>
    navigate({
      to: "/instances/$uuid",
      params: { uuid },
      search: tab ? { tab } : undefined,
    });

  const hasFilter = Boolean(status) || keyword.trim() !== "";
  const filtered = hasFilter || Boolean(q);
  const clearFilters = () => {
    setKeyword("");
    setSearch({ status: undefined });
  };
  const trueEmpty = !isLoading && !isError && !filtered && rows.length === 0;
  const count = isLoading || isError || hasNextPage ? undefined : visibleRows.length;
  const countsReady = status === undefined && !isLoading && !isError && !hasNextPage;
  const countOf = (n: number) => (countsReady ? n : undefined);
  const summaryItems = [
    { key: "all", label: t("instances.summaryAll"), count: countOf(rows.length) },
    {
      key: "running",
      label: t("shared:status.instance.running"),
      count: countOf(rows.filter((r) => r.status === "running").length),
    },
    {
      key: "stopped",
      label: t("shared:status.instance.stopped"),
      count: countOf(rows.filter((r) => r.status === "stopped").length),
    },
    {
      key: "attention",
      label: t("instances.summaryAttention"),
      tone: "warning" as const,
      count: countOf(attentionRows.length),
    },
  ];

  const emptyState = isError ? (
    <TableErrorEmpty isError onRetry={() => void refetch()} />
  ) : (
    <EmptyState
      scene="search"
      secondaryAction={
        <Button size="small" onClick={clearFilters}>
          {t("shared:filter.clear")}
        </Button>
      }
    />
  );
  const onboarding = (
    <div style={{ display: "flex", justifyContent: "center", padding: `${space.xl}px 0` }}>
      <OnboardingSteps />
    </div>
  );

  const body = narrow ? (
    isLoading ? (
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        {[0, 1, 2].map((i) => (
          <Card key={i} size="small">
            <Skeleton active title={{ width: "40%" }} paragraph={{ rows: 2 }} />
          </Card>
        ))}
      </Space>
    ) : visibleRows.length === 0 ? (
      trueEmpty ? (
        onboarding
      ) : (
        emptyState
      )
    ) : (
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        {visibleRows.map((r) => (
          <InstanceCard
            key={r.uuid}
            instance={r}
            summary={metrics}
            todayByInstance={todayByInstance}
            dailyReady={daily != null}
            freezeGraceHours={policies?.freeze_grace_hours}
            onShowEvents={() => void openDetail(r.uuid, "events")}
          />
        ))}
      </Space>
    )
  ) : trueEmpty ? (
    onboarding
  ) : (
    <Table<InstanceOut>
      rowKey="uuid"
      loading={isLoading}
      dataSource={visibleRows}
      pagination={false}
      scroll={{ x: 1000 }}
      sticky={{ offsetHeader: layout.topBarHeight }}
      locale={{ emptyText: isLoading ? <Skeleton active title={false} paragraph={{ rows: 3 }} /> : emptyState }}
      columns={[
        {
          title: t("instances.colName"),
          width: 220,
          fixed: "left",
          render: (_, r) => <InstanceNameCellMemo instance={r} />,
        },
        {
          title: t("instances.colStatus"),
          width: 140,
          render: (_, r) => <StatusCell instance={r} freezeGraceHours={policies?.freeze_grace_hours} />,
        },
        {
          title: t("instances.colSpec"),
          render: (_, r) => <SpecCell instance={r} />,
        },
        {
          title: t("instances.colUtil"),
          width: 160,
          render: (_, r) => <UtilCellMemo instance={r} summary={metrics} />,
        },
        {
          title: t("instances.colBilling"),
          render: (_, r) => <BillingCell instance={r} todayByInstance={todayByInstance} dailyReady={daily != null} />,
        },
        {
          title: t("instances.colActions"),
          fixed: "right",
          width: 250,
          render: (_, r) => <InstanceActions instance={r} onShowEvents={() => void openDetail(r.uuid, "events")} />,
        },
      ]}
    />
  );

  return (
    <PageContainer
      title={t("instances.title")}
      description={t("instances.description")}
      extra={
        <>
          <Button
            aria-label={t("instances.refreshList")}
            icon={<ReloadOutlined />}
            loading={isRefetching}
            onClick={() => void refetch()}
          />
          <Link to="/market">
            <Button type="primary">{t("instances.rentNew")}</Button>
          </Link>
        </>
      }
      freshness={{
        updatedAt: metricsQ.dataUpdatedAt,
        intervalMs: auto.intervalMs,
        paused: auto.paused,
        onTogglePause: auto.toggle,
        onRefresh: () => {
          void refetch();
          void metricsQ.refetch();
        },
        refreshing: isRefetching,
      }}
    >
      <AttentionBar items={attention} style={{ marginBottom: space.lg }} />
      {!trueEmpty && (
        <FilterBar hasFilter={hasFilter} onClear={clearFilters} count={count}>
          <StatusSummaryBar
            items={summaryItems}
            value={status ?? "all"}
            onChange={(k) => setSearch({ status: k === "all" ? undefined : k })}
            ariaLabel={t("instances.summaryAria")}
          />
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder={t("instances.searchPlaceholder")}
            aria-label={t("instances.searchPlaceholder")}
            data-search-input
            style={{ width: controlWidth.md }}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
        </FilterBar>
      )}
      {body}
      {!trueEmpty && (
        <LoadMore
          hasNextPage={hasNextPage}
          loading={isFetchingNextPage}
          isError={isFetchNextPageError}
          loadedCount={rows.length}
          onLoadMore={() => void fetchNextPage()}
        />
      )}
      {renewTarget && (
        <RenewModal key={renewTarget.uuid} instance={renewTarget} open onClose={() => setRenewTarget(null)} />
      )}
    </PageContainer>
  );
}
