/** 容器实例列表(默认落地页):页头(标题 + 主 CTA + 筛选)→ AttentionBar(公告 / 余额 / 到期 / 冻结 / 失败聚合成一条)→ 表格。
 *  服务端游标分页 + status/name 过滤(状态入 URL);名称即详情链接,改名走 hover 铅笔;主动作随状态变(连接 ▾ / 开机 / 重新创建);
 *  列表不挂 refetchInterval,过渡态由 useTransientInstanceRefresh 逐台轻轮询并在迁移时失效列表。 */

import { EditOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { type InstanceMetricsSummaryOut, type InstanceOut } from "@superdl/api-client";
import {
  controlWidth,
  fontSize,
  formatDateTime,
  instanceStatusMap,
  isBillingPeriod,
  localToday,
  metaOf,
  periodMap,
  POLL,
  space,
  useDebouncedValue,
} from "@superdl/ui";
import { LoadMore, moneyOr, PageHeader, TableErrorEmpty } from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  App,
  Button,
  Card,
  Grid,
  Input,
  Popover,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useEffect, useMemo, useRef, useState, memo } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";
import { useRenameInstance, useSetAutoRenew } from "../api/mutations";
import {
  useDailySummary,
  useExpiringInstances,
  useInstancePages,
  useMetricsSummary,
  usePolicies,
  useTransientInstanceRefresh,
} from "../api/queries";
import { InstanceStatusBadge, SpotReclaimTag, SpotTag, SubscriptionTag, TierTag } from "../components/common";
import { RenewModal } from "../components/RenewModal";
import { GpuSparkline } from "../components/GpuSparkline";
import { AttentionBar, useNotificationAttention, type AttentionItem } from "../components/AttentionBar";
import { InstanceActions } from "../components/InstanceActions";
import { OnboardingSteps } from "../components/OnboardingSteps";
import { requireAuth } from "../lib/guard";
import { listSearchStore } from "../stores/listSearch";

/** 可过滤的状态(released 终态不出列表) */
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
  // 列表状态入 URL;非法值回默认
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

/** 到期 / 冻结 / 失败三类实例条目(与通知类条目一起进 AttentionBar)。到期条目带「立即续费」「开启自动续费」。 */
function useInstanceAttention(rows: InstanceOut[], onRenew: (i: InstanceOut) => void): AttentionItem[] {
  const { t } = useTranslation(["web", "shared"]);
  const { formatExpiry, formatReclaimCountdown } = useFormat();
  const { message } = App.useApp();
  const { data: policies } = usePolicies();
  const { data: soon } = useExpiringInstances(policies?.period_expire_warn_days);
  const autoRenew = useSetAutoRenew(soon?.[0]?.uuid ?? "", {
    onSuccess: () => message.success(t("period.autoRenewOn")),
  });
  const items: AttentionItem[] = [];
  for (const inst of (soon ?? []).slice(0, 3)) {
    const sub = inst.subscription;
    if (!sub) continue;
    const periodKey = isBillingPeriod(sub.period) ? periodMap[sub.period].labelKey : null;
    items.push({
      key: `expiring:${inst.uuid}`,
      severity: "warning",
      title: t("attention.expiring", {
        name: inst.name,
        period: periodKey ? t(periodKey) : sub.period,
        left: formatExpiry(sub.expires_at) ?? "",
        time: formatDateTime(sub.expires_at),
      }),
      description: policies
        ? t("copy.periodExpirePolicy", { hours: policies.freeze_grace_hours })
        : t("copy.periodExpirePolicyFallback"),
      action: (
        <Space size={space.sm}>
          <Button size="small" type="primary" onClick={() => onRenew(inst)}>
            {t("period.renewNow")}
          </Button>
          {!sub.auto_renew && inst.uuid === soon?.[0]?.uuid && (
            <Button size="small" loading={autoRenew.isPending} onClick={() => autoRenew.mutate(true)}>
              {t("period.autoRenewOnMenu")}
            </Button>
          )}
        </Space>
      ),
    });
  }
  for (const inst of rows.filter((r) => r.status === "frozen").slice(0, 3)) {
    items.push({
      key: `frozen:${inst.uuid}`,
      severity: "error",
      title: t("attention.frozen", {
        name: inst.name,
        left: inst.frozen_deadline ? formatReclaimCountdown(inst.frozen_deadline) : "",
      }),
      action: (
        <Link to="/billing">
          <Button size="small" type="primary" danger>
            {t("attention.goRecharge")}
          </Button>
        </Link>
      ),
    });
  }
  for (const inst of rows.filter((r) => r.status === "failed").slice(0, 3)) {
    items.push({
      key: `failed:${inst.uuid}`,
      severity: "error",
      title: t("attention.failed", { name: inst.name }),
      action: (
        <Link to="/instances/$uuid" params={{ uuid: inst.uuid }} search={{ tab: "events" }}>
          <Button size="small">{t("attention.viewEvents")}</Button>
        </Link>
      ),
    });
  }
  return items;
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
    <Space size={8} align="center">
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

/** 计费列:包周期 = 档位标 + 周期价;按量 / 竞价 = 标记 + 时价 + 今日消费。续费入口在「更多」。 */
function BillingCell({
  instance,
  todayByInstance,
  dailyReady,
}: {
  instance: InstanceOut;
  /** instance_id → 当日已出账金额(十进制串) */
  todayByInstance: ReadonlyMap<number, string>;
  /** 日消费查询是否就绪(失败时显「—」) */
  dailyReady: boolean;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatHourlyPrice, formatMoney, formatPeriodPrice } = useFormat();
  const r = instance;
  return r.market === "subscription" && r.subscription ? (
    <Space orientation="vertical" size={0} align="start">
      <SubscriptionTag market={r.market} subscription={r.subscription} />
      <span>{formatPeriodPrice(r.subscription.amount_paid, r.subscription.period, r.subscription.period_count)}</span>
    </Space>
  ) : (
    // 竞价与按量共用;price_hourly 在竞价实例上已是折后价
    <Space orientation="vertical" size={0}>
      <Space size={6}>
        {r.market === "spot" ? (
          <SpotTag market={r.market} />
        ) : (
          <Tag style={{ marginInlineEnd: 0 }}>{t("instances.payAsYouGo")}</Tag>
        )}
        <span>{t("instances.pricePerCard", { price: formatHourlyPrice(r.price_hourly), count: r.gpu_count })}</span>
      </Space>
      <Space size={6}>
        <SpotReclaimTag market={r.market} />
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {/* 查询未就绪走 moneyOr 显「—」,不显假 ¥0.00(与详情页同口径) */}
          {t("instances.todayCost", { amount: moneyOr(formatMoney(todayByInstance.get(r.id)), dailyReady) })}
        </Typography.Text>
      </Space>
    </Space>
  );
}

/** 规格列:GPU 型号 × 数量 + 档位徽标,popover 展开完整配置(hover / focus / click 三触发,触屏可达)。 */
function SpecCell({ instance }: { instance: InstanceOut }) {
  const { t } = useTranslation(["web", "shared"]);
  return (
    <Popover
      trigger={["hover", "focus", "click"]}
      content={
        <Space orientation="vertical" size={2}>
          <span>{instance.spec["sku_name"] as string}</span>
          <span>
            {t("common.hostSpec", {
              vcpu: (instance.spec["vcpu"] as number) * instance.gpu_count,
              mem: (instance.spec["mem_gb"] as number) * instance.gpu_count,
              disk: instance.spec["disk_gb"] as number,
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
        <Typography.Text tabIndex={0} className="focus-ring" style={{ textDecoration: "underline dotted", cursor: "help" }}>
          {instance.spec["gpu_model"] as string} × {instance.gpu_count}
        </Typography.Text>
        <TierTag tier={instance.spec["tier"] as string} pool={instance.spec["pool_label"] as string} />
      </Space>
    </Popover>
  );
}

/** 名称列:名称即详情链接;hover 出铅笔进入行内改名(Enter 保存 / Esc 取消 / blur 保存)。 */
function NameCell({ instance }: { instance: InstanceOut }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(instance.name);
  // Esc 取消标记:随后的 blur 不再保存
  const cancelRef = useRef(false);
  const rename = useRenameInstance();
  const { message } = App.useApp();
  const save = async () => {
    if (cancelRef.current) {
      cancelRef.current = false;
      return;
    }
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
      // 错误提示由 useApiMutation 统一弹出;保持编辑态
    }
  };
  if (editing) {
    return (
      <Input
        size="small"
        autoFocus
        // 与创建页名称框同一上限(后端 64 字符)
        maxLength={64}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => void save()}
        onPressEnter={() => void save()}
        onKeyDown={(e) => {
          // Esc 恢复原值不提交
          if (e.key === "Escape") {
            cancelRef.current = true;
            setValue(instance.name);
            setEditing(false);
          }
        }}
        style={{ width: controlWidth.sm }}
      />
    );
  }
  return (
    <Space orientation="vertical" size={0} className="name-cell">
      <Space size={4} align="center">
        <Link to="/instances/$uuid" params={{ uuid: instance.uuid }} style={{ fontWeight: 600 }}>
          {instance.name}
        </Link>
        <Button
          type="text"
          size="small"
          className="row-hover-only"
          aria-label={t("instances.renameAria", { name: instance.name })}
          icon={<EditOutlined style={{ fontSize: fontSize.caption }} />}
          onClick={() => {
            cancelRef.current = false;
            setValue(instance.name);
            setEditing(true);
          }}
        />
      </Space>
      <Typography.Text type="secondary" className="mono" style={{ fontSize: fontSize.caption }}>
        {instance.uuid.slice(0, 12)}
      </Typography.Text>
    </Space>
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

const NameCellMemo = memo(
  NameCell,
  (prev, next) => prev.instance.uuid === next.instance.uuid && prev.instance.name === next.instance.name,
);

/** 移动端实例卡片(<md 替代表格):与表格共用 NameCell/StatusCell/SpecCell/UtilCell/BillingCell。 */
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
          <NameCellMemo instance={instance} />
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
  // <md 表格换卡片流;数据同源同游标
  const narrow = !Grid.useBreakpoint().md;
  // AttentionBar 到期条目与「更多」里的续费共用一个 modal
  const [renewTarget, setRenewTarget] = useState<InstanceOut | null>(null);
  const { q, status } = Route.useSearch();
  const [keyword, setKeyword] = useState(q ?? "");
  // 搜索输入防抖走共享 useDebouncedValue(300ms)
  const debouncedKeyword = useDebouncedValue(keyword, 300);
  const deferredQ = debouncedKeyword.trim();
  const { data: policies } = usePolicies();
  const pagesQ = useInstancePages({
    status,
    name: deferredQ || undefined,
  });
  const {
    data,
    isLoading,
    isError,
    refetch,
    isRefetching,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
    fetchNextPage,
  } = pagesQ;
  const { data: metrics } = useMetricsSummary({ refetchInterval: POLL.metrics });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: POLL.daily });

  const todayByInstance = useMemo(
    () => new Map((daily?.items ?? []).map((it) => [it.instance_id, it.total_amount])),
    [daily],
  );

  // 首页轮询替换与已加载旧页可能短暂重叠:按 uuid 去重(首页优先)
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
  // 过渡态实例逐台轻轮询(终态即停),迁移时失效列表查询
  useTransientInstanceRefresh(rows);

  // 公告 / 余额 / 欠费 + 到期 / 冻结 / 失败聚合成一条横幅
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

  // 记住当前筛选态,详情页「返回列表」带回
  useEffect(() => {
    listSearchStore.getState().remember("/instances", { q, status });
  }, [q, status]);

  // 列表状态入 URL(replace)
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

  // 空态表格/卡片共用:错误态 > 筛选无结果 > 真空态(新手引导三步)
  const emptyText = isError ? (
    <TableErrorEmpty isError onRetry={() => void refetch()} />
  ) : keyword || status ? (
    t("instances.noMatch")
  ) : (
    <div style={{ display: "flex", justifyContent: "center", padding: `${space.xl}px 0` }}>
      <OnboardingSteps />
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: space.lg }}>
      <PageHeader
        title={t("instances.title")}
        extra={
          <>
            <Select
              allowClear
              style={{ width: controlWidth.sm }}
              placeholder={t("instances.statusFilter")}
              aria-label={t("instances.statusFilter")}
              value={status ?? null}
              onChange={(v: string | null) => setSearch({ status: v ?? undefined })}
              options={FILTER_STATUSES.map((s) => {
                const meta = metaOf(instanceStatusMap, s);
                // 裸状态码不进 t()(extract 会当成新键)
                return { value: s, label: meta ? t(meta.labelKey) : s };
              })}
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
      />
      <AttentionBar items={attention} />
      {narrow ? (
        isLoading ? (
          <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
            {[0, 1, 2].map((i) => (
              <Card key={i} size="small">
                <Skeleton active title={{ width: "40%" }} paragraph={{ rows: 2 }} />
              </Card>
            ))}
          </Space>
        ) : rows.length === 0 ? (
          emptyText
        ) : (
          <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
            {rows.map((r) => (
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
      ) : (
        <Table<InstanceOut>
          rowKey="uuid"
          loading={isLoading}
          dataSource={rows}
          pagination={false}
          scroll={{ x: 1000 }}
          locale={{ emptyText }}
          columns={[
            {
              title: t("instances.colName"),
              width: 220,
              render: (_, r) => <NameCellMemo instance={r} />,
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
              render: (_, r) => (
                <BillingCell instance={r} todayByInstance={todayByInstance} dailyReady={daily != null} />
              ),
            },
            {
              title: t("instances.colActions"),
              fixed: "right",
              width: 260,
              render: (_, r) => (
                <InstanceActions instance={r} onShowEvents={() => void openDetail(r.uuid, "events")} />
              ),
            },
          ]}
        />
      )}
      <LoadMore
        hasNextPage={hasNextPage ?? false}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void fetchNextPage()}
      />
      {renewTarget && (
        <RenewModal key={renewTarget.uuid} instance={renewTarget} open onClose={() => setRenewTarget(null)} />
      )}
    </div>
  );
}
