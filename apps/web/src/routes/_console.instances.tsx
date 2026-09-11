/** 容器实例列表(默认落地页):策略提示条 + 动作行 + 密集表格。服务端游标分页 + status/name 过滤(状态入 URL);access/events 只在行展开时按需加载;列表不挂 refetchInterval,过渡态由 useTransientInstanceRefresh 逐台 5s 轮询并在迁移时失效列表。 */

import { CodeOutlined, CloseOutlined, DownOutlined, ReloadOutlined, SearchOutlined, UpOutlined } from "@ant-design/icons";
import { type InstanceMetricsSummaryOut, type InstanceOut } from "@superdl/api-client";
import {
  fontSize,
  formatDateTime,
  instanceStatusMap,
  isBillingPeriod,
  localToday,
  metaOf,
  periodMap,
  space,
  useDebouncedValue,
} from "@superdl/ui";
import { LoadMore, moneyOr, TableErrorEmpty } from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  Alert,
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
import { useRenameInstance } from "../api/mutations";
import {
  useDailySummary,
  useExpiringInstances,
  useInstanceAccess,
  useInstanceEvents,
  useInstancePages,
  useMetricsSummary,
  usePolicies,
  useTransientInstanceRefresh,
} from "../api/queries";
import { useSetAutoRenew } from "../api/mutations";
import {
  CopyButton,
  InstanceStatusBadge,
  SpotReclaimTag,
  SpotTag,
  SubscriptionTag,
  TierTag,
  useEventReasonText,
} from "../components/common";
import { RenewModal } from "../components/RenewModal";
import { GpuSparkline } from "../components/GpuSparkline";
import { AttentionBar, useNotificationAttention } from "../components/AttentionBar";
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

/** 展开行(running):SSH / Jupyter 快捷工具,access 仅在展开时拉取;接入信息字段可空,拿到什么渲染什么。 */
function ExpandedTools({ instance }: { instance: InstanceOut }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data: access, isError, refetch } = useInstanceAccess(instance.uuid);
  return (
    <Space size={12} wrap align="center">
      {isError ? (
        <TableErrorEmpty compact isError onRetry={() => void refetch()} />
      ) : access?.ssh_command ? (
        <CopyButton text={access.ssh_command} label="SSH" />
      ) : null}
      <Button
        size="small"
        icon={<CodeOutlined />}
        disabled={!access?.jupyter_url}
        onClick={() => {
          if (access?.jupyter_url) {
            window.open(access.jupyter_url, "_blank", "noopener,noreferrer");
          }
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
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("copy.jupyterNeedsRunning")}
        </Typography.Text>
      )}
    </Space>
  );
}

/** 展开行(failed):事件按需加载,给失败原因 + 未扣费 / 重新创建闭环。 */
function ExpandedFailed({ instance }: { instance: InstanceOut }) {
  const { t } = useTranslation();
  const reasonText = useEventReasonText();
  const { data: events, isError, refetch } = useInstanceEvents(instance.uuid);
  if (isError) {
    return <TableErrorEmpty compact isError onRetry={() => void refetch()} />;
  }
  // 服务端降序,最新一次 failed 原因取首元素
  const failedEvents = (events?.items ?? []).filter((e) => e.to_status === "failed");
  const reason = failedEvents[0]?.reason;
  const everRan = (events?.items ?? []).some((e) => e.to_status === "running");
  return (
    <Space orientation="vertical" size={4}>
      {reason ? (
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {reasonText(reason)}
        </Typography.Text>
      ) : null}
      {everRan ? (
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
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

/** 到期横幅正文。auto_renew 已开的实例只留「立即续费」。 */
function ExpiryBannerBody({
  instance,
  more,
  onRenew,
}: {
  instance: InstanceOut;
  /** 同样临期的其它实例台数(0 = 只有这一台) */
  more: number;
  onRenew: (i: InstanceOut) => void;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatExpiry } = useFormat();
  const { message } = App.useApp();
  const { data: policies } = usePolicies();
  const sub = instance.subscription;
  const autoRenew = useSetAutoRenew(instance.uuid, {
    onSuccess: () => message.success(t("period.autoRenewOn")),
  });
  if (!sub) return null;
  const periodKey = isBillingPeriod(sub.period) ? periodMap[sub.period].labelKey : null;
  return (
    <Alert
      type="warning"
      showIcon
      title={t("period.expiryBanner", {
        name: instance.name,
        period: periodKey ? t(periodKey) : sub.period,
        time: formatDateTime(sub.expires_at),
        left: formatExpiry(sub.expires_at) ?? "",
      })}
      description={
        <Space orientation="vertical" size={2}>
          <span>
            {policies
              ? t("copy.periodExpirePolicy", { hours: policies.freeze_grace_hours })
              : t("copy.periodExpirePolicyFallback")}
          </span>
          {more > 0 && <span>{t("period.expiryBannerMore", { count: more })}</span>}
        </Space>
      }
      action={
        <Space size={8}>
          <Button size="small" type="primary" onClick={() => onRenew(instance)}>
            {t("period.renewNow")}
          </Button>
          {!sub.auto_renew && (
            <Button size="small" loading={autoRenew.isPending} onClick={() => autoRenew.mutate(true)}>
              {t("period.autoRenewOnMenu")}
            </Button>
          )}
        </Space>
      }
    />
  );
}

/** 到期提醒:名下有临期(active 且剩余 ≤ period_expire_warn_days)的包周期实例时出;数据源 /instances/expiring(服务端过滤,不分页)。 */
function ExpiryBanner({ onRenew }: { onRenew: (i: InstanceOut) => void }) {
  const { data: policies } = usePolicies();
  const warnDays = policies?.period_expire_warn_days;
  const { data: soon } = useExpiringInstances(warnDays);
  const first = soon?.[0];
  if (!first) return null;
  return <ExpiryBannerBody instance={first} more={(soon?.length ?? 1) - 1} onRenew={onRenew} />;
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

/** 状态列(表格与移动卡片共用):stopped 时 tooltip 给冻结策略。 */
function StatusCell({
  instance,
  freezeGraceHours,
}: {
  instance: InstanceOut;
  freezeGraceHours?: number;
}) {
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

/** 计费列(表格与移动卡片共用):包周期 = 档位标 + 周期价 + 续费入口;按量/竞价 = 标记 + 时价 + 今日消费。到期信息取 InstanceOut.subscription。 */
function BillingCell({
  instance,
  todayByInstance,
  dailyReady,
  onRenew,
}: {
  instance: InstanceOut;
  /** instance_id → 当日已出账金额(十进制串) */
  todayByInstance: ReadonlyMap<number, string>;
  /** 日消费查询是否就绪(失败时显「—」) */
  dailyReady: boolean;
  onRenew: (i: InstanceOut) => void;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatHourlyPrice, formatMoney, formatPeriodPrice } = useFormat();
  const r = instance;
  return r.market === "subscription" && r.subscription ? (
    <Space orientation="vertical" size={0} align="start">
      <SubscriptionTag market={r.market} subscription={r.subscription} />
      <span>
        {formatPeriodPrice(
          r.subscription.amount_paid,
          r.subscription.period,
          r.subscription.period_count,
        )}
      </span>
      <Button
        size="small"
        type="link"
        style={{ paddingInline: 0, height: 20, fontSize: fontSize.caption }}
        onClick={() => onRenew(r)}
      >
        {t("period.renewMenu")}
      </Button>
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
        <span>
          {t("instances.pricePerCard", { price: formatHourlyPrice(r.price_hourly), count: r.gpu_count })}
        </span>
      </Space>
      <Space size={6}>
        <SpotReclaimTag market={r.market} />
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {/* 查询未就绪走 moneyOr 显「—」,不显假 ¥0.00(与详情页同口径) */}
          {t("instances.todayCost", {
            amount: moneyOr(formatMoney(todayByInstance.get(r.id)), dailyReady),
          })}
        </Typography.Text>
      </Space>
    </Space>
  );
}

/** 规格列:GPU 型号 × 数量 + 档位徽标,popover 展开完整配置。 */
function SpecCell({ instance }: { instance: InstanceOut }) {
  const { t } = useTranslation(["web", "shared"]);
  return (
    <Popover
      trigger={["hover", "focus"]}
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
        {/* Popover 仅 hover 触发时键盘不可达:规格文本可聚焦,focus 同触发 */}
        <Typography.Text tabIndex={0} style={{ textDecoration: "underline dotted" }}>
          {instance.spec["gpu_model"] as string} × {instance.gpu_count}
        </Typography.Text>
        <TierTag tier={instance.spec["tier"] as string} pool={instance.spec["pool_label"] as string} />
      </Space>
    </Popover>
  );
}

function NameCell({ instance, onDetail }: { instance: InstanceOut; onDetail: () => void }) {
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
          cancelRef.current = false;
          setValue(instance.name);
          setEditing(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            cancelRef.current = false;
            setValue(instance.name);
            setEditing(true);
          }
        }}
      >
        {instance.name}
      </Typography.Text>
      <Space size={8}>
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {instance.uuid.slice(0, 12)}
        </Typography.Text>
        <Button
          size="small"
          type="link"
          style={{ paddingInline: 0, height: 20, fontSize: fontSize.caption }}
          onClick={onDetail}
        >
          {t("instances.detail")}
        </Button>
      </Space>
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

/** 移动端实例卡片(<md 替代表格):与表格共用 NameCell/StatusCell/SpecCell/UtilCell/BillingCell;running 的快捷工具、failed 的失败原因折叠在卡内,展开才拉 access/events。 */
function InstanceCard({
  instance,
  summary,
  todayByInstance,
  dailyReady,
  freezeGraceHours,
  onRenew,
  onDetail,
  onShowEvents,
}: {
  instance: InstanceOut;
  summary: InstanceMetricsSummaryOut | undefined;
  todayByInstance: ReadonlyMap<number, string>;
  dailyReady: boolean;
  freezeGraceHours?: number;
  onRenew: (i: InstanceOut) => void;
  onDetail: () => void;
  onShowEvents: () => void;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const [expanded, setExpanded] = useState(false);
  const expandable = instance.status === "running" || instance.status === "failed";
  return (
    <Card size="small">
      <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
        {/* 第一行:实例名(改名/详情入口在 NameCell 内)+ 状态徽标 */}
        <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
          <NameCellMemo instance={instance} onDetail={onDetail} />
          <StatusCell instance={instance} freezeGraceHours={freezeGraceHours} />
        </Space>
        {/* 第二行:规格(GPU 型号×数量 + 档位/形态徽标) */}
        <SpecCell instance={instance} />
        {/* 第三行:利用率 sparkline + 计费信息 */}
        <Space size={space.lg} wrap align="start">
          <UtilCellMemo instance={instance} summary={summary} />
          <BillingCell
            instance={instance}
            todayByInstance={todayByInstance}
            dailyReady={dailyReady}
            onRenew={onRenew}
          />
        </Space>
        {/* 第四行:操作组 + 展开区开关 */}
        <Space size={space.sm} wrap>
          <InstanceActions instance={instance} onShowEvents={onShowEvents} />
          {expandable && (
            <Button
              size="small"
              type="text"
              aria-expanded={expanded}
              icon={expanded ? <UpOutlined /> : <DownOutlined />}
              onClick={() => setExpanded((e) => !e)}
            >
              {instance.status === "failed"
                ? t("instances.failedReason")
                : t("instances.quickTools")}
            </Button>
          )}
        </Space>
        {expanded &&
          (instance.status === "failed" ? (
            <ExpandedFailed instance={instance} />
          ) : (
            <ExpandedTools instance={instance} />
          ))}
      </Space>
    </Card>
  );
}

/** 「双击行查看详情」的一次性提示(localStorage 标记) */
const DBLCLICK_HINT_KEY = "superdl.dblclickHintSeen";

function InstancesPage() {
  const { t } = useTranslation(["web", "shared"]);
  const navigate = useNavigate();
  // <md 表格换卡片流;数据同源同游标
  const narrow = !Grid.useBreakpoint().md;
  // 横幅与「计费」列的续费入口共用一个 modal
  const [renewTarget, setRenewTarget] = useState<InstanceOut | null>(null);
  const { q, status } = Route.useSearch();
  const [keyword, setKeyword] = useState(q ?? "");
  // 搜索输入防抖走共享 useDebouncedValue(300ms)
  const debouncedKeyword = useDebouncedValue(keyword, 300);
  const deferredQ = debouncedKeyword.trim();
  const [dblclickHintSeen, setDblclickHintSeen] = useState(
    () => localStorage.getItem(DBLCLICK_HINT_KEY) === "1",
  );
  const { data: policies } = usePolicies();
  // 公告 / 余额预警 / 欠费聚合成一条横幅
  const attention = useNotificationAttention();
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
  const { data: metrics } = useMetricsSummary({ refetchInterval: 45_000 });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: 60_000 });

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
  // 过渡态实例逐台轻轮询(5s,终态即停),迁移时失效列表查询
  useTransientInstanceRefresh(rows);

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
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("instances.title")}
      </Typography.Title>
      <AttentionBar items={attention} />
      <ExpiryBanner onRenew={setRenewTarget} />
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
            loading={isRefetching}
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
            style={{ width: 220 }}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
        </Space>
      </Space>
      {!narrow && !dblclickHintSeen && (
        // 双击行进详情的一次性提示:关闭后写 localStorage;窄屏卡片流不出
        <Space size={4}>
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("instances.dblclickHint")}
          </Typography.Text>
          <Button
            size="small"
            type="text"
            aria-label={t("common.close")}
            icon={<CloseOutlined style={{ fontSize: fontSize.caption }} />}
            onClick={() => {
              localStorage.setItem(DBLCLICK_HINT_KEY, "1");
              setDblclickHintSeen(true);
            }}
          />
        </Space>
      )}
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
                onRenew={setRenewTarget}
                onDetail={() => void openDetail(r.uuid)}
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
        scroll={{ x: 960 }}
        expandable={{
          // 行展开按需加载:仅 running(工具)/ failed(原因)可展开
          rowExpandable: (r) => r.status === "running" || r.status === "failed",
          expandedRowRender: (r) =>
            r.status === "failed" ? <ExpandedFailed instance={r} /> : <ExpandedTools instance={r} />,
        }}
        locale={{ emptyText }}
        columns={[
          {
            title: t("instances.colName"),
            render: (_, r) => <NameCellMemo instance={r} onDetail={() => void openDetail(r.uuid)} />,
          },
          {
            title: t("instances.colStatus"),
            render: (_, r) => (
              <StatusCell instance={r} freezeGraceHours={policies?.freeze_grace_hours} />
            ),
          },
          {
            title: t("instances.colSpec"),
            render: (_, r) => <SpecCell instance={r} />,
          },
          {
            title: t("instances.colUtil"),
            render: (_, r) => <UtilCellMemo instance={r} summary={metrics} />,
          },
          {
            title: t("instances.colBilling"),
            render: (_, r) => (
              <BillingCell
                instance={r}
                todayByInstance={todayByInstance}
                dailyReady={daily != null}
                onRenew={setRenewTarget}
              />
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
      )}
      <LoadMore
        hasNextPage={hasNextPage ?? false}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void fetchNextPage()}
      />
      {renewTarget && (
        <RenewModal
          key={renewTarget.uuid}
          instance={renewTarget}
          open
          onClose={() => setRenewTarget(null)}
        />
      )}
    </Space>
  );
}
