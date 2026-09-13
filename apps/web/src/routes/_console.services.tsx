/** 在线服务列表:页头(标题 + 策略 ? tooltip + 主 CTA)→ FilterBar(状态 + 搜索,入 URL replace)→ 表格(名称 / 状态 / 服务端点 / 规格 / 版本 / 费用 / 创建时间 / 操作)。
 *  列表不轮询;deploying / stopping / releasing 逐条 5s 轮询,迁移即回刷;unready 不算过渡态。进详情走名称链接,不做双击行。 */

import { QuestionCircleOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import type { ServiceOut } from "@superdl/api-client";
import {
  controlWidth,
  fontSize,
  formatDateTime,
  isServiceStatus,
  layout,
  localToday,
  metaOf,
  POLL,
  SERVICE_FILTER_STATUSES,
  serviceStatusMap,
} from "@superdl/ui";
import {
  CopyButton,
  EmptyState,
  FilterBar,
  LoadMore,
  Mono,
  PageContainer,
  TableErrorEmpty,
} from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Button, Input, Select, Skeleton, Space, Table, Tag, theme, Tooltip, Typography } from "antd";
import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { useDailySummary, usePolicies, useServicePages, useTransientServiceRefresh } from "../api/queries";
import { BillingCell } from "../components/BillingCell";
import { ServiceStatusBadge, TierTag } from "../components/common";
import { ServiceActions } from "../components/services/ServiceActions";
import { requireAuth } from "../lib/guard";
import { useCursorList } from "../lib/useCursorList";

export interface ServicesSearch {
  q?: string;
  status?: string;
}

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

function ServicesPage() {
  const { t } = useTranslation(["web", "shared"]);
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const { q, status } = Route.useSearch();
  const { data: policies } = usePolicies();
  const commitQ = useCallback(
    (next: string | undefined) => void navigate({ to: "/services", search: { q: next, status }, replace: true }),
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
    usePages: (name) => useServicePages({ status, name }),
    keyOf: (s: ServiceOut) => s.slug,
  });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: POLL.daily });
  const todayByInstance = useMemo(
    () => new Map((daily?.items ?? []).map((it) => [it.instance_id, it.total_amount])),
    [daily],
  );
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

  const hasFilter = Boolean(status) || keyword.trim() !== "";
  // 已提交到 URL 的检索词也算筛选态:输入清空后 300ms 内行仍是旧结果,不能误判成真空态
  const filtered = hasFilter || Boolean(q);
  const clearFilters = () => {
    // 检索词经防抖回写 URL(与输入框清空同一路径);状态直接清
    setKeyword("");
    setSearch({ status: undefined });
  };
  // 计数只在全部加载完才显示
  const count = isLoading || isError || hasNextPage ? undefined : rows.length;

  // 错误态 > 筛选无结果 > 真空态;首载给骨架
  const emptyText = isError ? (
    <TableErrorEmpty isError onRetry={() => void refetch()} />
  ) : isLoading ? (
    <Skeleton active title={false} paragraph={{ rows: 3 }} />
  ) : filtered ? (
    <EmptyState
      scene="search"
      secondaryAction={
        <Button size="small" onClick={clearFilters}>
          {t("shared:filter.clear")}
        </Button>
      }
    />
  ) : (
    <EmptyState
      scene="list"
      description={
        <>
          {t("services.emptyTitle")}
          <br />
          {t("services.emptyHint")}
        </>
      }
      action={
        <Link to="/services/new">
          <Button type="primary">{t("services.deploy")}</Button>
        </Link>
      }
    />
  );

  return (
    <PageContainer
      title={t("services.title")}
      // 停机 / 冻结策略不做常驻条:放标题旁 tooltip(ui-ux-spec §1 规则 1);宿主可聚焦、可点
      tags={
        <Tooltip
          trigger={["hover", "focus", "click"]}
          title={
            policies?.freeze_grace_hours !== undefined
              ? t("services.policyBanner", { hours: policies.freeze_grace_hours })
              : t("services.policyBannerFallback")
          }
        >
          <QuestionCircleOutlined
            tabIndex={0}
            className="focus-ring"
            aria-label={t("services.policyHelp")}
            style={{ color: token.colorTextSecondary, cursor: "help" }}
          />
        </Tooltip>
      }
      extra={
        <>
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
    >
      <FilterBar hasFilter={hasFilter} onClear={clearFilters} count={count}>
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
      </FilterBar>
      <Table<ServiceOut>
        rowKey="slug"
        loading={isLoading}
        dataSource={rows}
        pagination={false}
        scroll={{ x: 1080 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        locale={{ emptyText }}
        columns={[
          {
            title: t("services.colName"),
            width: 220,
            fixed: "left",
            render: (_, r) => (
              <Space orientation="vertical" size={0}>
                <Link to="/services/$slug" params={{ slug: r.slug }}>
                  {r.name}
                </Link>
                <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                  <Mono>{r.slug}</Mono>
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
                    {inst.spec.gpu_model as string} × {inst.gpu_count}
                  </span>
                  <TierTag tier={inst.spec.tier as string} pool={inst.spec.pool_label as string} />
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
            render: (_, r) => (
              <BillingCell
                instance={r.current_instance ?? r.rollout_instance}
                todayByInstance={todayByInstance}
                dailyReady={daily != null}
              />
            ),
          },
          { title: t("services.colCreated"), render: (_, r) => formatDateTime(r.created_at) },
          {
            title: t("services.colActions"),
            fixed: "right",
            width: 220,
            render: (_, r) => <ServiceActions service={r} />,
          },
        ]}
      />
      <LoadMore
        hasNextPage={hasNextPage}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void fetchNextPage()}
      />
    </PageContainer>
  );
}
