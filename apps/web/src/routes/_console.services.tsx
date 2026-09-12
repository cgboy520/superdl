/** 在线服务列表:名称 / 状态 / 服务端点 / 规格 / 版本 / 费用 / 创建时间 / 操作;筛选与搜索入 URL(replace)。列表不轮询;deploying / stopping / releasing 逐条 5s 轮询,迁移即回刷;unready 不算过渡态。 */

import { POLL } from "@superdl/ui";
import { QuestionCircleOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import type { ServiceOut } from "@superdl/api-client";
import {
  controlWidth,
  fontSize,
  formatDateTime,
  isServiceStatus,
  localToday,
  metaOf,
  SERVICE_FILTER_STATUSES,
  serviceStatusMap,
  useDebouncedValue,
  useFormat,
} from "@superdl/ui";
import { LoadMore, moneyOr, PageHeader, TableErrorEmpty } from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Button, Input, Select, Space, Table, Tag, theme, Tooltip, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useDailySummary, usePolicies, useServicePages, useTransientServiceRefresh } from "../api/queries";
import {
  CopyButton,
  ServiceStatusBadge,
  SpotReclaimTag,
  SpotTag,
  SubscriptionTag,
  TierTag,
} from "../components/common";
import { ServiceActions } from "../components/services/ServiceActions";
import { requireAuth } from "../lib/guard";

export type ServicesSearch = { q?: string; status?: string };

/** 列表状态入 URL;非法值回默认(已删除不在筛选项里)。 */
export function servicesValidateSearch(search: Record<string, unknown>): ServicesSearch {
  const out: ServicesSearch = {};
  if (typeof search.q === "string" && search.q.trim()) out.q = search.q;
  if (isServiceStatus(search.status) && search.status !== "released") out.status = search.status;
  return out;
}

export const Route = createFileRoute("/_console/services")({
  beforeLoad: requireAuth,
  validateSearch: servicesValidateSearch,
  component: ServicesPage,
});

function hostOf(url: string): string {
  return url.replace(/^https?:\/\//, "");
}

/** 费用列:包周期 = 周期标 + 周期价;按量 / 竞价 = 标记 + 时价 + 今日消费(按当前版本实例匹配)。 */
function BillingCell({
  service,
  todayByInstance,
  dailyReady,
}: {
  service: ServiceOut;
  todayByInstance: ReadonlyMap<number, string>;
  dailyReady: boolean;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatHourlyPrice, formatMoney, formatPeriodPrice } = useFormat();
  const inst = service.current_instance ?? service.rollout_instance;
  if (!inst) return <Typography.Text type="secondary">—</Typography.Text>;
  if (inst.market === "subscription" && inst.subscription) {
    return (
      <Space orientation="vertical" size={0} align="start">
        <SubscriptionTag market={inst.market} subscription={inst.subscription} />
        <span>
          {formatPeriodPrice(inst.subscription.amount_paid, inst.subscription.period, inst.subscription.period_count)}
        </span>
      </Space>
    );
  }
  return (
    <Space orientation="vertical" size={0}>
      <Space size={6}>
        {inst.market === "spot" ? (
          <SpotTag market={inst.market} />
        ) : (
          <Tag style={{ marginInlineEnd: 0 }}>{t("instances.payAsYouGo")}</Tag>
        )}
        <span>
          {t("instances.pricePerCard", {
            price: formatHourlyPrice(inst.price_hourly),
            count: inst.gpu_count,
          })}
        </span>
      </Space>
      <Space size={6}>
        <SpotReclaimTag market={inst.market} />
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("instances.todayCost", {
            amount: moneyOr(formatMoney(todayByInstance.get(inst.id)), dailyReady),
          })}
        </Typography.Text>
      </Space>
    </Space>
  );
}

function ServicesPage() {
  const { t } = useTranslation(["web", "shared"]);
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const { q, status } = Route.useSearch();
  const [keyword, setKeyword] = useState(q ?? "");
  const debouncedKeyword = useDebouncedValue(keyword, 300);
  const deferredQ = debouncedKeyword.trim();
  const { data: policies } = usePolicies();
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
  } = useServicePages({ status, name: deferredQ || undefined });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: POLL.daily });
  const todayByInstance = useMemo(
    () => new Map((daily?.items ?? []).map((it) => [it.instance_id, it.total_amount])),
    [daily],
  );
  const rows = useMemo<ServiceOut[]>(() => {
    const seen = new Set<string>();
    const out: ServiceOut[] = [];
    for (const p of data?.pages ?? []) {
      for (const s of p.items) {
        if (seen.has(s.slug)) continue;
        seen.add(s.slug);
        out.push(s);
      }
    }
    return out;
  }, [data]);
  useTransientServiceRefresh(rows);

  const setSearch = (patch: ServicesSearch) =>
    void navigate({
      to: "/services",
      search: (prev: ServicesSearch) => {
        const merged = { ...prev, ...patch };
        return { q: merged.q || undefined, status: merged.status || undefined };
      },
      replace: true,
    });
  useEffect(() => {
    if ((q ?? "") === deferredQ) return;
    void navigate({ to: "/services", search: { q: deferredQ || undefined, status }, replace: true });
  }, [q, deferredQ, status, navigate]);

  const emptyText = isError ? (
    <TableErrorEmpty isError onRetry={() => void refetch()} />
  ) : keyword || status ? (
    t("services.noMatch")
  ) : (
    <TableErrorEmpty
      isError={false}
      action={
        <Link to="/services/new">
          <Button type="primary">{t("services.deploy")}</Button>
        </Link>
      }
    >
      <Space orientation="vertical" size={4}>
        <Typography.Text strong>{t("services.emptyTitle")}</Typography.Text>
        <Typography.Text type="secondary">{t("services.emptyHint")}</Typography.Text>
      </Space>
    </TableErrorEmpty>
  );

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      {/* 停机 / 冻结策略不做常驻条:放标题旁 tooltip(ui-ux-spec §1 规则 1) */}
      <PageHeader
        title={t("services.title")}
        tags={
          <Tooltip
            title={
              policies?.freeze_grace_hours !== undefined
                ? t("services.policyBanner", { hours: policies.freeze_grace_hours })
                : t("services.policyBannerFallback")
            }
          >
            <QuestionCircleOutlined style={{ color: token.colorTextSecondary, cursor: "help" }} />
          </Tooltip>
        }
        extra={
          <>
            <Select
              allowClear
              style={{ width: controlWidth.sm }}
              placeholder={t("services.statusFilter")}
              aria-label={t("services.statusFilter")}
              value={status ?? null}
              onChange={(v: string | null) => setSearch({ status: v ?? undefined })}
              options={SERVICE_FILTER_STATUSES.map((s) => {
                const meta = metaOf(serviceStatusMap, s);
                // 裸状态码不进 t()(extract 会当成新键)
                return { value: s, label: meta ? t(meta.labelKey) : s };
              })}
            />
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder={t("services.searchPlaceholder")}
              aria-label={t("services.searchPlaceholder")}
              data-search-input
              style={{ width: controlWidth.md }}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            <Button
              icon={<ReloadOutlined />}
              aria-label={t("instances.refreshList")}
              loading={isRefetching}
              onClick={() => void refetch()}
            />
            <Link to="/services/new">
              <Button type="primary">{t("services.deploy")}</Button>
            </Link>
          </>
        }
      />
      <Table<ServiceOut>
        rowKey="slug"
        loading={isLoading}
        dataSource={rows}
        pagination={false}
        scroll={{ x: 1080 }}
        locale={{ emptyText }}
        columns={[
          {
            title: t("services.colName"),
            render: (_, r) => (
              <Space orientation="vertical" size={0}>
                <Link to="/services/$slug" params={{ slug: r.slug }}>
                  {r.name}
                </Link>
                <Typography.Text type="secondary" code style={{ fontSize: fontSize.caption }}>
                  {r.slug}
                </Typography.Text>
              </Space>
            ),
          },
          {
            title: t("services.colStatus"),
            render: (_, r) => (
              <ServiceStatusBadge status={r.status} frozenDeadline={r.current_instance?.frozen_deadline} />
            ),
          },
          {
            title: t("services.colEndpoint"),
            render: (_, r) => (
              <Space orientation="vertical" size={0}>
                <Space size={6}>
                  <Typography.Text code>{hostOf(r.url)}</Typography.Text>
                  <CopyButton text={r.url} />
                </Space>
                <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                  {r.status === "running" || r.status === "unready"
                    ? `${r.ready ? t("services.ready") : t("services.notReady")} · ${
                        r.require_api_key ? t("services.authRequired") : t("services.authPublic")
                      }`
                    : t("services.endpointOffline")}
                </Typography.Text>
              </Space>
            ),
          },
          {
            title: t("services.colSpec"),
            render: (_, r) => {
              const inst = r.current_instance ?? r.rollout_instance;
              if (!inst) return "—";
              return (
                <Space>
                  <span>
                    {inst.spec["gpu_model"] as string} × {inst.gpu_count}
                  </span>
                  <TierTag tier={inst.spec["tier"] as string} pool={inst.spec["pool_label"] as string} />
                </Space>
              );
            },
          },
          {
            title: t("services.colRevision"),
            render: (_, r) => <Tag>{t("services.revisionTag", { no: r.revision })}</Tag>,
          },
          {
            title: t("services.colBilling"),
            render: (_, r) => <BillingCell service={r} todayByInstance={todayByInstance} dailyReady={daily != null} />,
          },
          { title: t("services.colCreated"), render: (_, r) => formatDateTime(r.created_at) },
          {
            title: t("services.colActions"),
            fixed: "right",
            render: (_, r) => <ServiceActions service={r} />,
          },
        ]}
        onRow={(r) => ({
          onDoubleClick: () => void navigate({ to: "/services/$slug", params: { slug: r.slug } }),
        })}
      />
      <LoadMore
        hasNextPage={hasNextPage ?? false}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void fetchNextPage()}
      />
    </Space>
  );
}
