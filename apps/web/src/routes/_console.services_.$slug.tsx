/** 服务详情:头部(名称 / 状态 / 版本 / 操作)+ 常驻服务端点卡 + Tab
 *  `概览 / 访问密钥 / 监控 / 日志 / 事件 / 账单 / 设置`(危险区在设置里)。
 *  只有一条服务轮询(过渡态 5s、运行中 30s、已删除停),端点卡与头部同源不再另打端点查询;
 *  监控与日志打的是当前版本实例。 */

import type { InstanceEventOut, ServiceOut } from "@superdl/api-client";
import { fontSize, formatDateTime, isTransientServiceStatus, localToday } from "@superdl/ui";
import { DataErrorAlert, moneyOr } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Alert, Breadcrumb, Card, Descriptions, Skeleton, Space, Tabs, Tag, Typography } from "antd";
import { useFormat } from "@superdl/ui";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useDailySummary,
  useService,
  useServiceApiKeys,
  useServiceBillPages,
  useServiceEventPages,
  useServiceLogs,
} from "../api/queries";
import {
  CopyButton,
  ServiceStatusBadge,
  SpotTag,
  SubscriptionTag,
  TierTag,
} from "../components/common";
import { HourlyBillsTable } from "../components/HourlyBillsTable";
import { EventsPanel } from "../components/instance/EventsPanel";
import { LogsPanel } from "../components/instance/LogsPanel";
import { MetricsPanel } from "../components/instance/MetricsPanel";
import { ApiKeysCard } from "../components/services/ApiKeysCard";
import { EndpointCard } from "../components/services/EndpointCard";
import { ServiceActions } from "../components/services/ServiceActions";
import { SettingsTab } from "../components/services/SettingsTab";
import { requireAuth } from "../lib/guard";

export const SERVICE_DETAIL_TABS = [
  "overview",
  "keys",
  "metrics",
  "logs",
  "events",
  "bills",
  "settings",
] as const;
export type ServiceDetailTab = (typeof SERVICE_DETAIL_TABS)[number];

/** tab 白名单:非法值(含旧链接的 ?tab=service)回退默认 Tab,不渲染无选中态的 Tabs。 */
export function serviceDetailValidateSearch(search: Record<string, unknown>): { tab?: ServiceDetailTab } {
  const tab = search["tab"];
  return typeof tab === "string" && (SERVICE_DETAIL_TABS as readonly string[]).includes(tab)
    ? { tab: tab as ServiceDetailTab }
    : {};
}

export const Route = createFileRoute("/_console/services_/$slug")({
  beforeLoad: requireAuth,
  validateSearch: serviceDetailValidateSearch,
  component: ServiceDetail,
});

/** 概览:当前版本的容器配置回显 + 调用示例。密文 env 只显示键名(接口只回 env_secret_keys)。 */
function OverviewTab({ service }: { service: ServiceOut }) {
  const { t } = useTranslation();
  const c = service.container;
  const keysQ = useServiceApiKeys(service.slug, { enabled: service.require_api_key });
  // 调用示例里的 Key 用「某把未吊销 Key 的前缀 + 省略号」占位,不拿明文(明文根本不在这儿)
  const livePrefix = (keysQ.data ?? []).find((k) => k.revoked_at == null)?.key_prefix;
  const curl = [
    `curl ${service.url}`,
    ...(service.require_api_key
      ? [`  -H "Authorization: Bearer ${livePrefix ?? "sk-xxxxxxxx"}…"`]
      : []),
  ].join(" \\\n");
  const envRows = c
    ? [
        ...Object.entries(c.env).map(([name, value]) => ({ name, value, secret: false })),
        ...c.env_secret_keys.map((name) => ({ name, value: "", secret: true })),
      ].sort((a, b) => a.name.localeCompare(b.name))
    : [];
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Card size="small" title={t("services.detail.currentRevision", { no: service.revision })}>
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <Descriptions
            size="small"
            column={{ xs: 1, sm: 2 }}
            items={[
              // 镜像地址独占一行:五个单格 + 三个双格会让某一行凑不齐 column,antd 会告警
              { label: t("services.detail.imageLabel"), span: { xs: 1, sm: 2 }, children: c?.image_ref ?? "—" },
              { label: t("services.detail.portLabel"), children: c?.service_port ?? "—" },
              {
                label: t("services.detail.healthLabel"),
                children: c?.health_path ?? t("services.detail.healthNone"),
              },
              {
                label: t("services.detail.authLabel"),
                children: service.require_api_key
                  ? t("services.authRequired")
                  : t("services.authPublic"),
              },
              {
                label: "SSH",
                children: c?.with_ssh ? t("services.detail.sshOn") : t("services.detail.sshOff"),
              },
              {
                label: t("services.detail.commandLabel"),
                span: { xs: 1, sm: 2 },
                children:
                  c?.container_command && c.container_command.length > 0 ? (
                    <Typography.Text code>{c.container_command.join(" ")}</Typography.Text>
                  ) : (
                    <Typography.Text type="secondary">
                      {t("services.detail.commandDefault")}
                    </Typography.Text>
                  ),
              },
              {
                label: t("services.detail.argsLabel"),
                span: { xs: 1, sm: 2 },
                // 创建时是一行一个参数,这里也一行一个:join 成一串会让带空格的参数分不出边界
                children:
                  c?.container_args && c.container_args.length > 0 ? (
                    <Space orientation="vertical" size={2}>
                      {c.container_args.map((arg, i) => (
                        <Typography.Text key={`${i}-${arg}`} code>
                          {arg}
                        </Typography.Text>
                      ))}
                    </Space>
                  ) : (
                    <Typography.Text type="secondary">{t("services.detail.none")}</Typography.Text>
                  ),
              },
              {
                label: t("services.detail.envLabel"),
                span: { xs: 1, sm: 2 },
                children:
                  envRows.length > 0 ? (
                    <Space orientation="vertical" size={2}>
                      {envRows.map((row) => (
                        <Typography.Text key={row.name} code>
                          {row.name}={row.secret ? "••••••" : row.value}
                          {row.secret && (
                            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                              {` (${t("services.detail.envSecret")})`}
                            </Typography.Text>
                          )}
                        </Typography.Text>
                      ))}
                    </Space>
                  ) : (
                    <Typography.Text type="secondary">{t("services.detail.none")}</Typography.Text>
                  ),
              },
            ]}
          />
          <Typography.Text type="secondary">{t("services.detail.configImmutableNote")}</Typography.Text>
        </Space>
      </Card>
      <Card size="small" title={t("services.detail.curlCard")}>
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <pre
            style={{
              margin: 0,
              fontSize: fontSize.caption,
              lineHeight: 1.8,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {curl}
          </pre>
          <CopyButton text={curl} label={t("instances.copyCommand")} />
          {service.require_api_key && (
            <Typography.Text type="secondary">
              {t("services.detail.curlKeyPlaceholderNote")}
            </Typography.Text>
          )}
        </Space>
      </Card>
    </Space>
  );
}

function LogsTab({ service }: { service: ServiceOut }) {
  const [tail, setTail] = useState(200);
  const [autoRefresh, setAutoRefresh] = useState(true);
  // 部署中最需要启动日志:deploying / running / unready 都可读(容器已建出即有日志)
  const viewable =
    service.status === "deploying" || service.status === "running" || service.status === "unready";
  const { data, error, refetch } = useServiceLogs(
    service.slug,
    { tail_lines: tail },
    { enabled: viewable, refetchInterval: autoRefresh ? 10_000 : false, retry: 0 },
  );
  const lines = useMemo(() => data?.lines ?? [], [data]);
  return (
    <LogsPanel
      viewable={viewable}
      lines={lines}
      truncated={data?.truncated}
      error={error}
      onRetry={() => void refetch()}
      tail={tail}
      onTail={setTail}
      autoRefresh={autoRefresh}
      onAutoRefresh={setAutoRefresh}
      downloadName={service.slug}
    />
  );
}

function EventsTab({ slug, status }: { slug: string; status: string }) {
  const queryClient = useQueryClient();
  // 服务轮询检测到 status 迁移后失效事件查询:立即 + 3s 延迟各刷一次,覆盖事件行落库略晚的窗口
  const prevStatus = useRef(status);
  useEffect(() => {
    if (prevStatus.current === status) return;
    prevStatus.current = status;
    const key = ["services", slug, "events"];
    void queryClient.invalidateQueries({ queryKey: key });
    const timer = setTimeout(() => void queryClient.invalidateQueries({ queryKey: key }), 3_000);
    return () => clearTimeout(timer);
  }, [status, slug, queryClient]);
  const {
    data,
    isLoading,
    isError,
    refetch,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
    fetchNextPage,
  } = useServiceEventPages(slug);
  const events = useMemo<InstanceEventOut[]>(
    () => (data?.pages ?? []).flatMap((p) => p.items),
    [data],
  );
  return (
    <EventsPanel
      events={events}
      isLoading={isLoading}
      isError={isError}
      onRetry={() => void refetch()}
      hasNextPage={hasNextPage ?? false}
      isFetchingNextPage={isFetchingNextPage}
      isFetchNextPageError={isFetchNextPageError}
      onLoadMore={() => void fetchNextPage()}
    />
  );
}

function BillsTab({ slug }: { slug: string }) {
  return <HourlyBillsTable query={useServiceBillPages(slug)} />;
}

function ServiceDetail() {
  const { t } = useTranslation(["web", "shared"]);
  const { formatHourlyPrice, formatMoney, formatPeriodPrice } = useFormat();
  const { slug } = Route.useParams();
  const { tab } = Route.useSearch();
  const navigate = useNavigate();
  const {
    data: service,
    isError: serviceError,
    refetch: refetchService,
  } = useService(slug, {
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      if (!s || s === "released") return false;
      return isTransientServiceStatus(s) ? 5_000 : 30_000;
    },
  });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: 60_000 });

  if (serviceError && !service) {
    return <DataErrorAlert onRetry={() => void refetchService()} />;
  }
  if (!service) {
    return (
      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
        <Card>
          <Skeleton active title={{ width: 240 }} paragraph={{ rows: 2 }} />
        </Card>
        <Card>
          <Skeleton active paragraph={{ rows: 6 }} />
        </Card>
      </Space>
    );
  }
  const inst = service.current_instance ?? service.rollout_instance;
  const live = service.status === "running" || service.status === "unready";
  const todayAmount = (inst && daily?.items.find((it) => it.instance_id === inst.id)?.total_amount) ?? null;
  const activeTab: ServiceDetailTab = tab ?? "overview";
  const goTab = (k: string, replace: boolean) =>
    void navigate({ to: "/services/$slug", params: { slug }, search: { tab: k as ServiceDetailTab }, replace });

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Breadcrumb
        items={[{ title: <Link to="/services">{t("services.title")}</Link> }, { title: service.name }]}
      />
      <Card>
        <Space style={{ width: "100%", justifyContent: "space-between" }} align="start" wrap>
          <Space orientation="vertical" size={4}>
            <Space wrap>
              <Typography.Title level={4} style={{ margin: 0 }}>
                {service.name}
              </Typography.Title>
              <ServiceStatusBadge status={service.status} frozenDeadline={inst?.frozen_deadline} />
              {inst && (
                <TierTag tier={inst.spec["tier"] as string} pool={inst.spec["pool_label"] as string} />
              )}
              <Tag>{t("services.revisionTag", { no: service.revision })}</Tag>
              {inst && <SubscriptionTag market={inst.market} subscription={inst.subscription} />}
              {inst && <SpotTag market={inst.market} />}
            </Space>
            <Descriptions
              size="small"
              column={{ xs: 1, sm: 2, md: 3, xl: 4 }}
              items={[
                { label: t("services.detail.labelId"), children: service.slug },
                {
                  label: t("services.detail.labelSpec"),
                  children: inst
                    ? t("instances.specLine", { model: inst.spec["gpu_model"] as string, count: inst.gpu_count })
                    : "—",
                },
                {
                  label: t("services.detail.labelBilling"),
                  children: inst
                    ? inst.subscription
                      ? formatPeriodPrice(
                          inst.subscription.amount_paid,
                          inst.subscription.period,
                          inst.subscription.period_count,
                        )
                      : t("instances.pricePerCard", {
                          price: formatHourlyPrice(inst.price_hourly),
                          count: inst.gpu_count,
                        })
                    : "—",
                },
                ...(inst?.subscription
                  ? [
                      {
                        label: t("services.detail.labelExpiresAt"),
                        children: formatDateTime(inst.subscription.expires_at),
                      },
                    ]
                  : [
                      {
                        label: t("services.detail.labelToday"),
                        children: moneyOr(formatMoney(todayAmount), daily != null),
                      },
                    ]),
                { label: t("services.detail.createdAt"), children: formatDateTime(service.created_at) },
              ]}
            />
          </Space>
          <ServiceActions service={service} onDeleted={() => void navigate({ to: "/services" })} />
        </Space>
      </Card>

      <EndpointCard service={service} onShowLogs={() => goTab("logs", false)} />

      <Tabs
        activeKey={activeTab}
        // Tab activeKey 入 URL 用 replace(ui-ux-spec §1-8):连点六个 Tab 后退一次就该回列表
        onChange={(k) => goTab(k, true)}
        items={[
          { key: "overview", label: t("services.detail.tabOverview"), children: <OverviewTab service={service} /> },
          {
            key: "keys",
            label: t("services.detail.tabKeys"),
            children: (
              <ApiKeysCard
                slug={service.slug}
                requireApiKey={service.require_api_key}
                released={service.released_at != null}
              />
            ),
          },
          {
            key: "metrics",
            label: t("services.detail.tabMetrics"),
            children: inst ? (
              <MetricsPanel uuid={inst.uuid} running={live} />
            ) : (
              <Alert type="info" showIcon title={t("services.detail.metricsNotRunning")} />
            ),
          },
          { key: "logs", label: t("services.detail.tabLogs"), children: <LogsTab service={service} /> },
          {
            key: "events",
            label: t("services.detail.tabEvents"),
            children: <EventsTab slug={service.slug} status={service.status} />,
          },
          { key: "bills", label: t("services.detail.tabBills"), children: <BillsTab slug={service.slug} /> },
          {
            key: "settings",
            label: t("services.detail.tabSettings"),
            children: (
              <SettingsTab
                // 换服务时重置本地草稿(名称输入框)
                key={service.slug}
                service={service}
                onGoKeys={() => goTab("keys", false)}
                onDeleted={() => void navigate({ to: "/services" })}
              />
            ),
          },
        ]}
      />
    </Space>
  );
}
