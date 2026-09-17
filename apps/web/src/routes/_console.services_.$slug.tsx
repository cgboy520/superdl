/** Service detail: endpoint, overview, keys, monitoring, logs, history and settings; revision updates use a drawer. */

import { POLL, space } from "@superdl/ui";
import type { InstanceOut, ServiceOut } from "@superdl/api-client";
import { fontSize, formatDateTime, instanceStatusMap, isTransientServiceStatus, localToday, metaOf } from "@superdl/ui";
import {
  CopyButton,
  DataErrorAlert,
  EntityHeader,
  KeyValue,
  moneyOr,
  PageContainer,
  TableErrorEmpty,
} from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Alert, App, Badge, Breadcrumb, Card, Grid, Skeleton, Space, Table, Tabs, Tag, Typography } from "antd";
import { useFormat } from "@superdl/ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useUpdateService } from "../api/mutations";
import {
  useDailySummary,
  useService,
  useServiceApiKeys,
  useServiceBillPages,
  useServiceRevisions,
} from "../api/queries";
import { ServiceStatusBadge, SpotTag, SubscriptionTag, TierTag } from "../components/common";
import { HourlyBillsTable } from "../components/HourlyBillsTable";
import { EventsTab } from "../components/instance/EventsTab";
import { LogsTab } from "../components/instance/LogsTab";
import { MetricsPanel } from "../components/instance/MetricsPanel";
import { ApiKeysCard } from "../components/services/ApiKeysCard";
import { EndpointCard } from "../components/services/EndpointCard";
import { RevisionDrawer } from "../components/services/RevisionDrawer";
import { ServiceActions } from "../components/services/ServiceActions";
import { SettingsTab } from "../components/services/SettingsTab";
import { requireAuth } from "../lib/guard";

export const SERVICE_DETAIL_TABS = ["overview", "keys", "metrics", "logs", "history", "settings"] as const;
export type ServiceDetailTab = (typeof SERVICE_DETAIL_TABS)[number];

/** Tab allow-list; invalid values fall back to the default tab. */
export function serviceDetailValidateSearch(search: Record<string, unknown>): { tab?: ServiceDetailTab } {
  const tab = search.tab;
  if (typeof tab !== "string") return {};
  return (SERVICE_DETAIL_TABS as readonly string[]).includes(tab) ? { tab: tab as ServiceDetailTab } : {};
}

export const Route = createFileRoute("/_console/services_/$slug")({
  beforeLoad: requireAuth,
  validateSearch: serviceDetailValidateSearch,
  component: ServiceDetail,
});

/** Overview: echo of the current revision's container configuration + call example. Secret env shows key names only (the API returns env_secret_keys only). */
function OverviewTab({ service }: { service: ServiceOut }) {
  const { t } = useTranslation();
  const c = service.container;
  const screens = Grid.useBreakpoint();
  const full = screens.sm ? 2 : 1;
  const keysQ = useServiceApiKeys(service.slug, { enabled: service.require_api_key });
  const livePrefix = (keysQ.data ?? []).find((k) => k.revoked_at == null)?.key_prefix;
  const curl = [
    `curl ${service.url}`,
    ...(service.require_api_key ? [`  -H "Authorization: Bearer ${livePrefix ?? "sk-xxxxxxxx"}…"`] : []),
  ].join(" \\\n");
  const envRows = c
    ? [
        ...Object.entries(c.env).map(([name, value]) => ({ name, value, secret: false })),
        ...c.env_secret_keys.map((name) => ({ name, value: "", secret: true })),
      ].sort((a, b) => a.name.localeCompare(b.name))
    : [];
  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
      <Card size="small" title={t("services.detail.currentRevision", { no: service.revision })}>
        <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
          <KeyValue
            columns={{ xs: 1, sm: 2 }}
            items={[
              { label: t("services.detail.imageLabel"), span: full, value: c?.image_ref, mono: true },
              { label: t("services.detail.portLabel"), value: c?.service_port },
              {
                label: t("services.detail.healthLabel"),
                value: c?.health_path ?? t("services.detail.healthNone"),
              },
              {
                label: t("services.detail.authLabel"),
                value: service.require_api_key ? t("services.authRequired") : t("services.authPublic"),
              },
              {
                label: "SSH",
                value: c?.with_ssh ? t("services.detail.sshOn") : t("services.detail.sshOff"),
              },
              {
                label: t("services.detail.commandLabel"),
                span: full,
                value:
                  c?.container_command && c.container_command.length > 0 ? (
                    <Typography.Text code>{c.container_command.join(" ")}</Typography.Text>
                  ) : (
                    <Typography.Text type="secondary">{t("services.detail.commandDefault")}</Typography.Text>
                  ),
              },
              {
                label: t("services.detail.argsLabel"),
                span: full,
                value:
                  c?.container_args && c.container_args.length > 0 ? (
                    <Space orientation="vertical" size={space.xs}>
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
                span: full,
                value:
                  envRows.length > 0 ? (
                    <Space orientation="vertical" size={space.xs}>
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
        <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
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
            <Typography.Text type="secondary">{t("services.detail.curlKeyPlaceholderNote")}</Typography.Text>
          )}
        </Space>
      </Card>
    </Space>
  );
}

/** Revision history: the first 50 by instance ID descending (released included), no paging; the current revision is marked. */
function RevisionsTab({ service }: { service: ServiceOut }) {
  const { t } = useTranslation(["web", "shared"]);
  const q = useServiceRevisions(service.slug);
  const rows = q.data?.items ?? [];
  const currentUuid = service.current_instance?.uuid;
  return (
    <Table<InstanceOut>
      rowKey="uuid"
      size="small"
      pagination={false}
      loading={q.isLoading}
      scroll={{ x: 720 }}
      dataSource={rows}
      locale={{
        emptyText: q.isError ? (
          <TableErrorEmpty isError onRetry={() => void q.refetch()} />
        ) : (
          t("services.revision.empty")
        ),
      }}
      columns={[
        {
          title: t("services.revision.colRevision"),
          width: 120,
          render: (_, r) => (
            <Space size={space.xs}>
              <span>v{r.service_revision ?? "?"}</span>
              {r.uuid === currentUuid && <Tag color="blue">{t("services.revision.currentTag")}</Tag>}
            </Space>
          ),
        },
        {
          title: t("services.revision.colStatus"),
          width: 110,
          render: (_, r) => {
            const m = metaOf(instanceStatusMap, r.status);
            return <Badge status={m?.badge} color={m?.color} text={m ? t(m.labelKey) : r.status} />;
          },
        },
        {
          title: t("services.revision.colImage"),
          render: (_, r) => <Typography.Text code>{r.image_ref}</Typography.Text>,
        },
        {
          title: t("services.revision.colInstance"),
          width: 120,
          render: (_, r) => (
            <Link to="/instances/$uuid" params={{ uuid: r.uuid }}>
              {r.uuid.slice(0, 8)}
            </Link>
          ),
        },
        {
          title: t("services.revision.colCreated"),
          width: 170,
          render: (_, r) => formatDateTime(r.created_at),
        },
      ]}
    />
  );
}

function BillsTab({ slug }: { slug: string }) {
  return <HourlyBillsTable query={useServiceBillPages(slug)} />;
}

function ServiceDetail() {
  const { t } = useTranslation(["web", "shared"]);
  const { message } = App.useApp();
  const { formatHourlyPrice, formatMoney, formatPeriodPrice } = useFormat();
  const { slug } = Route.useParams();
  const { tab } = Route.useSearch();
  const navigate = useNavigate();
  const [revisionOpen, setRevisionOpen] = useState(false);
  const {
    data: service,
    isError: serviceError,
    refetch: refetchService,
  } = useService(slug, {
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      if (!s || s === "released") return false;
      return isTransientServiceStatus(s) ? POLL.transient : POLL.steady;
    },
  });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: POLL.daily });
  const update = useUpdateService(slug);

  if (serviceError && !service) {
    return (
      <PageContainer>
        <DataErrorAlert onRetry={() => void refetchService()} />
      </PageContainer>
    );
  }
  if (!service) {
    return (
      <PageContainer>
        <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
          <Card>
            <Skeleton active title={{ width: 240 }} paragraph={{ rows: 2 }} />
          </Card>
          <Card>
            <Skeleton active paragraph={{ rows: 6 }} />
          </Card>
        </Space>
      </PageContainer>
    );
  }
  const inst = service.current_instance ?? service.rollout_instance;
  const live = service.status === "running" || service.status === "unready";
  const released = service.released_at != null;
  const todayAmount = (inst && daily?.items.find((it) => it.instance_id === inst.id)?.total_amount) ?? "0.00";
  const requested: ServiceDetailTab = tab ?? "overview";
  const activeTab: ServiceDetailTab = requested === "keys" && !service.require_api_key ? "settings" : requested;
  const goTab = (k: string, replace: boolean) =>
    void navigate({ to: "/services/$slug", params: { slug }, search: { tab: k as ServiceDetailTab }, replace });

  return (
    <PageContainer>
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        <Breadcrumb items={[{ title: <Link to="/services">{t("services.title")}</Link> }, { title: service.name }]} />
        <EntityHeader
          name={service.name}
          status={<ServiceStatusBadge status={service.status} frozenDeadline={inst?.frozen_deadline} />}
          tags={
            <>
              {inst && <TierTag tier={inst.spec.tier as string} pool={inst.spec.pool_label as string} />}
              <Tag>{t("services.revisionTag", { no: service.revision })}</Tag>
              {inst && <SubscriptionTag market={inst.market} subscription={inst.subscription} />}
              {inst && <SpotTag market={inst.market} />}
            </>
          }
          meta={[
            { label: t("services.detail.labelId"), value: service.slug, mono: true, copy: service.slug },
            {
              label: t("services.detail.labelSpec"),
              value: inst
                ? t("instances.specLine", { model: inst.spec.gpu_model as string, count: inst.gpu_count })
                : null,
            },
            {
              label: t("services.detail.labelBilling"),
              value: inst
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
                : null,
            },
            inst?.subscription
              ? {
                  label: t("services.detail.labelExpiresAt"),
                  value: formatDateTime(inst.subscription.expires_at),
                }
              : {
                  label: t("services.detail.labelToday"),
                  value: moneyOr(formatMoney(todayAmount), daily != null),
                },
            { label: t("services.detail.createdAt"), value: formatDateTime(service.created_at) },
          ]}
          rename={
            released
              ? undefined
              : {
                  onSave: async (next) => {
                    await update.mutateAsync({ name: next });
                    message.success(t("services.settings.saved"));
                  },
                  ariaLabel: t("instances.renameAria", { name: service.name }),
                  maxLength: 64,
                }
          }
          actions={
            <ServiceActions
              service={service}
              size="middle"
              onDeleted={() => void navigate({ to: "/services" })}
              onRollout={() => setRevisionOpen(true)}
            />
          }
        />

        <EndpointCard
          service={service}
          onShowLogs={() => goTab("logs", false)}
          onShowEvents={() => goTab("history", false)}
        />

        <Tabs
          activeKey={activeTab}
          onChange={(k) => goTab(k, true)}
          items={[
            {
              key: "overview",
              label: t("services.detail.tabOverview"),
              children: (
                <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
                  <OverviewTab service={service} />
                  <Card size="small" title={t("services.detail.tabBills")}>
                    <BillsTab slug={service.slug} />
                  </Card>
                </Space>
              ),
            },
            ...(service.require_api_key
              ? [
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
                ]
              : []),
            {
              key: "metrics",
              label: t("services.detail.tabMetrics"),
              children: inst ? (
                <MetricsPanel uuid={inst.uuid} running={live} />
              ) : (
                <Alert type="info" showIcon title={t("services.detail.metricsNotRunning")} />
              ),
            },
            {
              key: "logs",
              label: t("services.detail.tabLogs"),
              children: (
                <LogsTab
                  subject={{ kind: "service", slug: service.slug }}
                  viewable={["deploying", "running", "unready"].includes(service.status)}
                />
              ),
            },
            {
              key: "history",
              label: t("services.detail.tabHistory"),
              children: (
                <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
                  <Card size="small" title={t("services.detail.tabRevisions")}>
                    <RevisionsTab service={service} />
                  </Card>
                  <Card size="small" title={t("services.detail.tabEvents")}>
                    <EventsTab subject={{ kind: "service", slug: service.slug }} status={service.status} />
                  </Card>
                </Space>
              ),
            },
            {
              key: "settings",
              label: t("services.detail.tabSettings"),
              children: (
                <SettingsTab
                  key={service.slug}
                  service={service}
                  onGoKeys={() => goTab("keys", false)}
                  onDeleted={() => void navigate({ to: "/services" })}
                />
              ),
            },
          ]}
        />
        <RevisionDrawer service={service} open={revisionOpen} onClose={() => setRevisionOpen(false)} />
      </Space>
    </PageContainer>
  );
}
