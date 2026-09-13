/** 实例详情:EntityHeader(名称行内改名 / 状态 / 标签 / 元信息 / 操作组)+ 连接 / 监控 / 日志 / 事件时间线(= 计费依据)/ 账单 / 设置(只剩危险区)。默认 Tab 按状态(running → 连接,其余 → 事件);事件/账单 Tab 游标分页;面包屑返回列表不丢筛选态。服务的版本实例不进列表但直链可达:「连接」按 with_ssh 出 SSH 卡,Jupyter 卡不出。 */

import { type InstanceOut } from "@superdl/api-client";
import { formatDateTime, isTransientInstanceStatus, localToday, POLL, space } from "@superdl/ui";
import {
  CopyField,
  DangerZone,
  DataErrorAlert,
  EntityHeader,
  moneyOr,
  PageContainer,
  useConfirm,
} from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Alert, App, Breadcrumb, Button, Card, Skeleton, Space, Tabs, Typography } from "antd";
import { useFormat } from "@superdl/ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useRenameInstance, useResetJupyterToken } from "../api/mutations";
import { useDailySummary, useHourlyBillPages, useInstance, useInstanceAccess } from "../api/queries";
import { InstanceStatusBadge, SpotTag, SubscriptionTag, TierTag } from "../components/common";
import { HourlyBillsTable } from "../components/HourlyBillsTable";
import { EventsTab } from "../components/instance/EventsTab";
import { LogsTab } from "../components/instance/LogsTab";
import { MetricsPanel } from "../components/instance/MetricsPanel";
import { InstanceActions, ReleaseModal, canReleaseStatus } from "../components/InstanceActions";
import { requireAuth } from "../lib/guard";
import { useRememberedListSearch } from "../stores/listSearch";

// 旧链接的 ?tab=service 不在白名单,回默认 Tab
const DETAIL_TABS = ["access", "metrics", "logs", "events", "bills", "settings"] as const;

export const Route = createFileRoute("/_console/instances_/$uuid")({
  beforeLoad: requireAuth,
  validateSearch: (search: Record<string, unknown>): { tab?: string } => {
    // tab 白名单:非法值回默认 Tab
    const tab = search.tab;
    return typeof tab === "string" && (DETAIL_TABS as readonly string[]).includes(tab) ? { tab } : {};
  },
  component: InstanceDetail,
});

/** 连接:SSH 卡按 with_ssh 出,Jupyter 卡只对开发机出;接入信息字段可空,拿到什么渲染什么。 */
function AccessTab({ instance, running }: { instance: InstanceOut; running: boolean }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const { data: access } = useInstanceAccess(instance.uuid, { enabled: running });
  const reset = useResetJupyterToken();
  const isService = instance.workload_type === "service";
  if (!running) {
    return <Alert type="info" showIcon title={t("instances.accessNotRunning")} />;
  }
  // 服务型 + 不开 SSH:指到「服务」Tab
  if (isService && !instance.with_ssh) {
    return <Alert type="info" showIcon title={t("instances.accessServiceOnly")} />;
  }
  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
      {instance.with_ssh && (
        <Card size="small" title="SSH">
          <Space orientation="vertical">
            {access?.ssh_command ? (
              <CopyField value={access.ssh_command} code label={t("instances.copyCommand")} />
            ) : (
              <Typography.Text code>…</Typography.Text>
            )}
            <Typography.Text type="secondary">{t("copy.sshKeyOnly")}</Typography.Text>
          </Space>
        </Card>
      )}
      {!isService && (
        <Card size="small" title="JupyterLab">
          <Space>
            <Button
              type="primary"
              disabled={!access?.jupyter_url}
              onClick={() => {
                if (access?.jupyter_url) {
                  window.open(access.jupyter_url, "_blank", "noopener,noreferrer");
                }
              }}
            >
              {t("instances.openJupyter")}
            </Button>
            <Button
              onClick={() =>
                confirm({
                  title: t("instances.resetTokenConfirmTitle"),
                  consequences: [t("instances.resetTokenConfirmBody")],
                  onOk: async () => {
                    await reset.mutateAsync(instance.uuid);
                    message.success(t("instances.tokenReset"));
                  },
                })
              }
            >
              {t("instances.resetToken")}
            </Button>
          </Space>
        </Card>
      )}
    </Space>
  );
}

function BillsTab({ instanceId }: { instanceId: number }) {
  return <HourlyBillsTable query={useHourlyBillPages({ instance_id: instanceId })} />;
}

/** 设置 Tab:只有危险区(改名在头部完成,ui-ux-spec §3.3)。 */
function SettingsTab({ canRelease, onRelease }: { canRelease: boolean; onRelease: () => void }) {
  const { t } = useTranslation();
  return (
    <DangerZone
      title={t("instances.dangerZone")}
      description={t("instances.dangerNote")}
      actions={[
        {
          key: "release",
          label: t("instances.release"),
          onClick: onRelease,
          disabled: !canRelease,
          disabledReason: t("copy.releaseNeedsStopped"),
        },
      ]}
    />
  );
}

function InstanceDetail() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { formatHourlyPrice, formatMoney, formatPeriodPrice } = useFormat();
  // 头部行内改名(设置 Tab 不再有改名卡)
  const rename = useRenameInstance();
  const { uuid } = Route.useParams();
  const { tab } = Route.useSearch();
  const navigate = useNavigate();
  const [releaseOpen, setReleaseOpen] = useState(false);
  const listSearch = useRememberedListSearch("/instances") as { q?: string; status?: string };
  const {
    data: instance,
    isError: instanceError,
    refetch: refetchInstance,
  } = useInstance(uuid, {
    refetchInterval: (q) =>
      q.state.data && isTransientInstanceStatus(q.state.data.status) ? POLL.transient : POLL.steady,
  });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: POLL.daily });
  const todayAmount = (instance && daily?.items.find((it) => it.instance_id === instance.id)?.total_amount) ?? "0.00";

  if (instanceError && !instance) {
    return (
      <PageContainer>
        <DataErrorAlert onRetry={() => void refetchInstance()} />
      </PageContainer>
    );
  }
  // 首载骨架
  if (!instance) {
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
  const running = instance.status === "running";
  const canRelease = canReleaseStatus(instance.status);
  // 默认 Tab 按状态:running 最想做的是连接;其余(停机 / 失败 / 过渡态)看事件最有用
  const activeTab = tab ?? (running ? "access" : "events");

  return (
    <PageContainer>
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        {/* 面包屑:带回列表页最近一次筛选态(stores/listSearch) */}
        <Breadcrumb
          items={[
            {
              title: (
                <Link to="/instances" search={listSearch}>
                  {t("instances.title")}
                </Link>
              ),
            },
            { title: instance.name },
          ]}
        />
        <EntityHeader
          name={instance.name}
          status={<InstanceStatusBadge status={instance.status} frozenDeadline={instance.frozen_deadline} />}
          tags={
            <>
              <TierTag tier={instance.spec.tier as string} pool={instance.spec.pool_label as string} />
              <SubscriptionTag market={instance.market} subscription={instance.subscription} />
              <SpotTag market={instance.market} />
            </>
          }
          meta={[
            { label: t("instances.labelId"), value: instance.uuid, mono: true, copy: instance.uuid },
            {
              label: t("instances.labelSpec"),
              value: t("instances.specLine", {
                model: instance.spec.gpu_model as string,
                count: instance.gpu_count,
              }),
            },
            {
              label: t("instances.labelBilling"),
              // 包周期实例时价是折后价且不出小时账,不报「¥X/时 × N 卡」
              value: instance.subscription
                ? formatPeriodPrice(
                    instance.subscription.amount_paid,
                    instance.subscription.period,
                    instance.subscription.period_count,
                  )
                : t("instances.pricePerCard", {
                    price: formatHourlyPrice(instance.price_hourly),
                    count: instance.gpu_count,
                  }),
            },
            instance.subscription
              ? {
                  label: t("instances.labelExpiresAt"),
                  value: formatDateTime(instance.subscription.expires_at),
                }
              : {
                  label: t("instances.labelToday"),
                  value: moneyOr(formatMoney(todayAmount), daily != null),
                },
            { label: t("instances.createdAt"), value: formatDateTime(instance.created_at) },
          ]}
          rename={{
            onSave: async (next) => {
              await rename.mutateAsync({ uuid: instance.uuid, name: next });
              message.success(t("instances.renamed"));
            },
            ariaLabel: t("instances.renameAria", { name: instance.name }),
            maxLength: 64,
          }}
          actions={
            /* 「事件日志」跳到本页事件 Tab */
            <InstanceActions
              instance={instance}
              size="middle"
              onShowEvents={() =>
                void navigate({ to: "/instances/$uuid", params: { uuid }, search: { tab: "events" } })
              }
            />
          }
        />

        <Tabs
          activeKey={activeTab}
          onChange={(k) =>
            // Tab activeKey 入 URL 用 replace(ui-ux-spec §1-8)
            void navigate({ to: "/instances/$uuid", params: { uuid }, search: { tab: k }, replace: true })
          }
          items={[
            {
              key: "access",
              label: t("instances.tabAccess"),
              children: <AccessTab instance={instance} running={running} />,
            },
            {
              key: "metrics",
              label: t("instances.tabMetrics"),
              children: <MetricsPanel uuid={uuid} running={running} />,
            },
            {
              key: "logs",
              label: t("instances.tabLogs"),
              children: (
                <LogsTab
                  subject={{ kind: "instance", uuid }}
                  viewable={instance.status === "running" || instance.status === "stopping"}
                />
              ),
            },
            {
              key: "events",
              label: t("instances.tabEvents"),
              children: <EventsTab subject={{ kind: "instance", uuid }} status={instance.status} />,
            },
            {
              key: "bills",
              label: t("instances.tabBills"),
              children: <BillsTab instanceId={instance.id} />,
            },
            {
              key: "settings",
              label: t("instances.tabSettings"),
              children: <SettingsTab canRelease={canRelease} onRelease={() => setReleaseOpen(true)} />,
            },
          ]}
        />
        <ReleaseModal
          instance={instance}
          open={releaseOpen}
          onClose={() => setReleaseOpen(false)}
          onReleased={() => void navigate({ to: "/instances", search: listSearch })}
        />
      </Space>
    </PageContainer>
  );
}
