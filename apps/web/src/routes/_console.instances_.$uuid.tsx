/** 实例详情:监控(降级文案)/服务/连接/事件时间线(=计费依据)/账单 + 危险区释放。
 *  事件/账单两 Tab 走游标分页;面包屑返回列表不丢筛选态。
 *  「服务」Tab 只对 workload_type='service' 出;服务形态的「连接」按 with_ssh 决定出不出 SSH 卡,
 *  Jupyter 卡一律不出(服务型实例根本没建 Jupyter 入口)。 */

import {
  isApiError,
  type ApiKeyOut,
  type BillHourlyOut,
  type InstanceEventOut,
  type InstanceOut,
} from "@superdl/api-client";
import { formatDateTime, isTransientInstanceStatus, localToday } from "@superdl/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  Alert,
  App,
  Badge,
  Breadcrumb,
  Button,
  Card,
  Descriptions,
  Radio,
  Select,
  Skeleton,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  theme,
  Timeline,
  Typography,
} from "antd";
import { useFormat } from "../lib/format";
import EChart from "../components/EChart";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useResetJupyterToken, useRevokeApiKey } from "../api/mutations";
import {
  useApiKeys,
  useDailySummary,
  useHourlyBillPages,
  useInstance,
  useInstanceAccess,
  useInstanceEventPages,
  useInstanceLogs,
  useInstanceMetrics,
  useServiceEndpoint,
} from "../api/queries";
import { ApiKeyModal } from "../components/ApiKeyModal";
import {
  CopyButton,
  InstanceStatusBadge,
  SpotTag,
  SubscriptionTag,
  TierTag,
  useEventReasonText,
} from "../components/common";
import { InstanceActions, ReleaseModal, canReleaseStatus } from "../components/InstanceActions";
import { DataErrorAlert, moneyOr, TableErrorEmpty } from "../components/QueryState";
import { requireAuth } from "../lib/guard";

// service 只对服务型实例出;白名单照收(dev 实例带 ?tab=service 进来时下面回退默认 Tab)
const DETAIL_TABS = ["metrics", "service", "access", "logs", "events", "bills"] as const;

export const Route = createFileRoute("/_console/instances_/$uuid")({
  beforeLoad: requireAuth,
  validateSearch: (search: Record<string, unknown>): { tab?: string } => {
    // tab 白名单:非法值回退默认 Tab,不渲染无选中态的 Tabs
    const tab = search["tab"];
    return typeof tab === "string" && (DETAIL_TABS as readonly string[]).includes(tab)
      ? { tab }
      : {};
  },
  component: InstanceDetail,
});

const SERIES_META = {
  gpu_util: { nameKey: "instances.seriesGpu", unit: "%" },
  vram_used_mb: { nameKey: "instances.seriesVram", unit: "MB" },
  cpu_pct: { nameKey: "instances.seriesCpu", unit: "%" },
  mem_used_mb: { nameKey: "instances.seriesMem", unit: "MB" },
} as const;

function MetricsTab({ uuid, running }: { uuid: string; running: boolean }) {
  const { t } = useTranslation();
  const [range, setRange] = useState<"1h" | "6h" | "24h">("1h");
  const { data, error, isLoading, refetch } = useInstanceMetrics(
    uuid,
    { range },
    { enabled: running, refetchInterval: 60_000, retry: 0 },
  );

  if (!running) {
    return <Alert type="info" showIcon title={t("instances.metricsNotRunning")} />;
  }
  // 503 = 监控源未接入/断源(专用文案,不影响计费);其余错误绝不能静默渲染成空图
  if (error && isApiError(error) && error.status === 503) {
    return <Alert type="warning" showIcon title={t("copy.monitoringDown")} />;
  }
  if (error) {
    return <DataErrorAlert onRetry={() => void refetch()} />;
  }
  const series = (data?.series ?? {}) as Record<string, [number, number][]>;
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Radio.Group
        value={range}
        onChange={(e) => setRange(e.target.value as typeof range)}
        optionType="button"
        options={[
          { value: "1h", label: t("instances.range1h") },
          { value: "6h", label: t("instances.range6h") },
          { value: "24h", label: t("instances.range24h") },
        ]}
      />
      {Object.entries(SERIES_META).map(([key, meta]) => (
        <Card key={key} size="small" title={t(meta.nameKey)} loading={isLoading}>
          <EChart
            style={{ height: 180 }}
            option={{
              grid: { left: 48, right: 16, top: 16, bottom: 24 },
              xAxis: { type: "time" },
              yAxis: { type: "value", axisLabel: { formatter: `{value}${meta.unit}` } },
              tooltip: { trigger: "axis" },
              series: [
                {
                  type: "line",
                  showSymbol: false,
                  areaStyle: { opacity: 0.08 },
                  data: (series[key] ?? []).map(([ts, v]) => [ts * 1000, v]),
                },
              ],
            }}
          />
        </Card>
      ))}
    </Space>
  );
}

/** 连接:SSH 卡按 with_ssh 出,Jupyter 卡只对开发机出。
 *  接入信息的每个字段都可空,必须按「拿到什么渲染什么」写。 */
function AccessTab({ instance, running }: { instance: InstanceOut; running: boolean }) {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const { data: access } = useInstanceAccess(instance.uuid, { enabled: running });
  const reset = useResetJupyterToken();
  const isService = instance.workload_type === "service";
  if (!running) {
    return <Alert type="info" showIcon title={t("instances.accessNotRunning")} />;
  }
  // 服务型 + 不开 SSH:这个 Tab 没有任何入口,直接把人指到「服务」Tab,不留一张空卡
  if (isService && !instance.with_ssh) {
    return <Alert type="info" showIcon title={t("instances.accessServiceOnly")} />;
  }
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      {instance.with_ssh && (
        <Card size="small" title="SSH">
          <Space orientation="vertical">
            <Typography.Text code>{access?.ssh_command}</Typography.Text>
            {access?.ssh_command && (
              <CopyButton text={access.ssh_command} label={t("instances.copyCommand")} />
            )}
            <Typography.Text type="secondary">{t("copy.sshKeyOnly")}</Typography.Text>
          </Space>
        </Card>
      )}
      {/* 服务型实例不建 Jupyter 入口:这张卡一律不出,而不是出一个点了没反应的按钮 */}
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
                modal.confirm({
                  title: t("instances.resetTokenConfirmTitle"),
                  content: t("instances.resetTokenConfirmBody"),
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

/** 服务:端点 + API Key + 调用示例 + 容器配置回显,仅 workload_type='service' 渲染。
 *  就绪为「否」不是故障态(持续 not-ready 也留在 running),必须如实显示而不渲染成红色报错。 */
function ServiceTab({ instance, onShowLogs }: { instance: InstanceOut; onShowLogs: () => void }) {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const running = instance.status === "running";
  const [newKeyOpen, setNewKeyOpen] = useState(false);
  const epQ = useServiceEndpoint(instance.uuid, {
    // 就绪位跟着 Pod readiness 变,running 时 30s 刷一次;非 running 不轮询
    refetchInterval: running ? 30_000 : false,
  });
  const keysQ = useApiKeys(instance.uuid);
  const revoke = useRevokeApiKey(instance.uuid, {
    onSuccess: () => message.success(t("instances.apiKeyRevokedMsg")),
  });
  const ep = epQ.data;
  const keys = keysQ.data ?? [];
  // 调用示例里的 Key 用「某把未吊销 Key 的前缀 + 省略号」占位,不拿明文(明文根本不在这儿)
  const livePrefix = keys.find((k) => k.revoked_at == null)?.key_prefix;
  const curl = ep
    ? [
        `curl ${ep.url}`,
        ...(ep.require_api_key
          ? [`  -H "Authorization: Bearer ${livePrefix ?? "sk-xxxxxxxx"}…"`]
          : []),
      ].join(" \\\n")
    : "";

  // 容器配置回显的环境变量:明文项来自 env(有值),密文项只有键名(后端不回密文的值)
  const envRows = ep
    ? [
        ...Object.entries(ep.env).map(([name, value]) => ({ name, value, secret: false })),
        ...ep.env_secret_keys.map((name) => ({ name, value: "", secret: true })),
      ].sort((a, b) => a.name.localeCompare(b.name))
    : [];

  if (epQ.isError) {
    return <DataErrorAlert onRetry={() => void epQ.refetch()} />;
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Card size="small" title={t("instances.serviceEndpointCard")} loading={!ep}>
        {ep && (
          <Space orientation="vertical" size={8} style={{ width: "100%" }}>
            <Space wrap size={8}>
              <Typography.Text code style={{ fontSize: 15 }}>
                {ep.url}
              </Typography.Text>
              <CopyButton text={ep.url} label={t("instances.copyEndpoint")} />
            </Space>
            <Space wrap size={12}>
              <Typography.Text type="secondary">
                {t("instances.serviceEndpointLine", {
                  port: ep.container_port,
                  health: ep.health_path ?? t("instances.serviceHealthNone"),
                })}
              </Typography.Text>
              <Badge
                status={ep.ready ? "success" : "default"}
                text={ep.ready ? t("instances.serviceReady") : t("instances.serviceNotReady")}
              />
              <Tag>
                {ep.require_api_key
                  ? t("instances.serviceAuthRequired")
                  : t("instances.serviceAuthPublic")}
              </Tag>
            </Space>
            {!running && <Alert type="info" showIcon title={t("instances.serviceNotRunning")} />}
            {running && !ep.ready && (
              // 持续 not-ready 的服务实例不判 failed,这里不渲染成错误态
              <Alert
                type="info"
                showIcon
                title={t("copy.serviceNotReadyHint")}
                action={
                  <Button size="small" onClick={onShowLogs}>
                    {t("instances.serviceCheckLogs")}
                  </Button>
                }
              />
            )}
          </Space>
        )}
      </Card>

      <Card
        size="small"
        title={t("instances.apiKeyCard")}
        extra={
          <Button type="primary" size="small" onClick={() => setNewKeyOpen(true)}>
            {t("instances.apiKeyNew")}
          </Button>
        }
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          {ep && !ep.require_api_key && (
            <Alert type="warning" showIcon title={t("instances.apiKeyPublicNote")} />
          )}
          <Table<ApiKeyOut>
            rowKey="id"
            size="small"
            pagination={false}
            loading={keysQ.isLoading}
            dataSource={keys}
            locale={{
              emptyText: keysQ.isError ? (
                <TableErrorEmpty onRetry={() => void keysQ.refetch()} />
              ) : (
                t("instances.apiKeyEmpty")
              ),
            }}
            columns={[
              { title: t("instances.apiKeyColName"), render: (_, r) => r.name },
              {
                title: t("instances.apiKeyColKey"),
                render: (_, r) => <Typography.Text code>{r.key_prefix}…</Typography.Text>,
              },
              {
                title: t("instances.apiKeyColLastUsed"),
                render: (_, r) =>
                  r.last_used_at ? formatDateTime(r.last_used_at) : t("instances.apiKeyNeverUsed"),
              },
              {
                title: t("instances.apiKeyColCreated"),
                render: (_, r) => formatDateTime(r.created_at),
              },
              {
                title: t("instances.apiKeyColActions"),
                render: (_, r) =>
                  r.revoked_at ? (
                    <Typography.Text type="secondary">{t("instances.apiKeyRevoked")}</Typography.Text>
                  ) : (
                    <Button
                      size="small"
                      danger
                      onClick={() =>
                        modal.confirm({
                          title: t("instances.apiKeyRevokeConfirmTitle"),
                          content: t("instances.apiKeyRevokeConfirmBody"),
                          okButtonProps: { danger: true },
                          onOk: () => revoke.mutateAsync(r.id),
                        })
                      }
                    >
                      {t("instances.apiKeyRevoke")}
                    </Button>
                  ),
              },
            ]}
          />
          <Typography.Text type="secondary">{t("copy.apiKeyOnce")}</Typography.Text>
        </Space>
      </Card>

      <Card size="small" title={t("instances.curlCard")}>
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <pre
            style={{
              margin: 0,
              fontSize: 13,
              lineHeight: 1.8,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {curl}
          </pre>
          {curl && <CopyButton text={curl} label={t("instances.copyCommand")} />}
          {ep?.require_api_key && (
            <Typography.Text type="secondary">{t("instances.curlKeyPlaceholderNote")}</Typography.Text>
          )}
        </Space>
      </Card>

      <Card size="small" title={t("instances.containerConfigCard")}>
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <Descriptions
            size="small"
            column={{ xs: 1, sm: 2 }}
            items={[
              { label: t("instances.containerConfigImage"), children: instance.image_ref },
              { label: t("instances.containerConfigPort"), children: ep?.container_port ?? "—" },
              { label: t("instances.containerConfigProtocol"), children: ep?.protocol ?? "—" },
              {
                label: t("instances.containerConfigHealth"),
                children: ep?.health_path ?? t("instances.serviceHealthNone"),
              },
              {
                label: t("instances.containerConfigAuth"),
                children: ep
                  ? ep.require_api_key
                    ? t("instances.serviceAuthRequired")
                    : t("instances.serviceAuthPublic")
                  : "—",
              },
              {
                label: "SSH",
                children: instance.with_ssh
                  ? t("instances.containerConfigSshOn")
                  : t("instances.containerConfigSshOff"),
              },
              {
                label: t("instances.containerConfigCommand"),
                span: 2,
                children:
                  ep?.container_command && ep.container_command.length > 0 ? (
                    <Typography.Text code>{ep.container_command.join(" ")}</Typography.Text>
                  ) : (
                    <Typography.Text type="secondary">
                      {t("instances.containerConfigCommandDefault")}
                    </Typography.Text>
                  ),
              },
              {
                label: t("instances.containerConfigArgs"),
                span: 2,
                // 创建时是一行一个参数,这里也一行一个:join 成一串会让带空格的参数分不出边界
                children:
                  ep?.container_args && ep.container_args.length > 0 ? (
                    <Space orientation="vertical" size={2}>
                      {ep.container_args.map((arg, i) => (
                        <Typography.Text key={`${i}-${arg}`} code>
                          {arg}
                        </Typography.Text>
                      ))}
                    </Space>
                  ) : (
                    <Typography.Text type="secondary">{t("instances.containerConfigNone")}</Typography.Text>
                  ),
              },
              {
                label: t("instances.containerConfigEnv"),
                span: 2,
                children: envRows.length > 0 ? (
                  <Space orientation="vertical" size={2}>
                    {envRows.map((row) => (
                      <Typography.Text key={row.name} code>
                        {row.name}={row.secret ? "••••••" : row.value}
                        {row.secret && (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {` (${t("instances.containerConfigEnvSecret")})`}
                          </Typography.Text>
                        )}
                      </Typography.Text>
                    ))}
                  </Space>
                ) : (
                  <Typography.Text type="secondary">{t("instances.containerConfigNone")}</Typography.Text>
                ),
              },
            ]}
          />
          {/* 密文变量的值后端刻意不回:回了这个端点就成了「把密文读回明文」的入口 */}
          <Typography.Text type="secondary">{t("instances.containerConfigImmutable")}</Typography.Text>
        </Space>
      </Card>

      <ApiKeyModal uuid={instance.uuid} open={newKeyOpen} onClose={() => setNewKeyOpen(false)} />
    </Space>
  );
}

const LOG_TAIL_OPTIONS = [100, 200, 500, 1000];

function LogsTab({ uuid, viewable }: { uuid: string; viewable: boolean }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const [tail, setTail] = useState(200);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { data, error, refetch } = useInstanceLogs(
    uuid,
    { tail_lines: tail },
    // 页面不可见时间隔轮询自动暂停(react-query 默认,未开 refetchIntervalInBackground)
    { enabled: viewable, refetchInterval: autoRefresh ? 10_000 : false, retry: 0 },
  );
  const lines = useMemo(() => data?.lines ?? [], [data]);

  // 自动跟随:新日志到达即滚到底部(最新行在末尾)
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  if (!viewable) {
    return <Alert type="info" showIcon title={t("instances.logsNotRunning")} />;
  }

  const download = () => {
    const blob = new Blob([`${lines.join("\n")}\n`], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${uuid}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Space orientation="vertical" size={12} style={{ width: "100%" }}>
      <Space wrap size={12}>
        <Typography.Text type="secondary">{t("instances.logsTail")}</Typography.Text>
        <Select
          value={tail}
          style={{ width: 104 }}
          options={LOG_TAIL_OPTIONS.map((n) => ({ value: n, label: String(n) }))}
          onChange={setTail}
        />
        <Switch
          checked={autoRefresh}
          onChange={setAutoRefresh}
          aria-label={t("instances.logsAutoRefresh")}
        />
        <Typography.Text>{t("instances.logsAutoRefresh")}</Typography.Text>
        <Button size="small" disabled={lines.length === 0} onClick={download}>
          {t("instances.logsDownload")}
        </Button>
        {data?.truncated && (
          <Typography.Text type="secondary">
            {t("instances.logsTruncatedNote", { lines: tail })}
          </Typography.Text>
        )}
      </Space>
      {error ? (
        <DataErrorAlert onRetry={() => void refetch()} />
      ) : (
        <div
          ref={scrollRef}
          style={{
            height: 420,
            overflow: "auto",
            padding: "8px 12px",
            background: token.colorFillQuaternary,
            border: `1px solid ${token.colorBorderSecondary}`,
            borderRadius: token.borderRadius,
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
            fontSize: 12,
            lineHeight: 1.7,
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
          }}
        >
          {lines.length === 0 ? (
            <Typography.Text type="secondary">{t("instances.logsEmpty")}</Typography.Text>
          ) : (
            lines.map((line, i) => <div key={i}>{line}</div>)
          )}
        </div>
      )}
    </Space>
  );
}

function EventsTab({ uuid, status }: { uuid: string; status?: string }) {
  const { t } = useTranslation();
  const reasonText = useEventReasonText();
  // 时间线即计费依据:过渡态必须跟着状态一起刷新;游标分页 + 加载更多
  const { data, isError, refetch, hasNextPage, isFetchingNextPage, fetchNextPage } =
    useInstanceEventPages(uuid);
  const events = useMemo<InstanceEventOut[]>(
    () => (data?.pages ?? []).flatMap((p) => p.items),
    [data],
  );
  // 过渡态 5s 轮询(单实例事件量小,重取已加载页代价可忽略);稳态不轮询
  const transient = status != null && isTransientInstanceStatus(status);
  useEffect(() => {
    if (!transient) return;
    const timer = setInterval(() => void refetch(), 5_000);
    return () => clearInterval(timer);
  }, [transient, refetch]);
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Alert type="info" showIcon title={t("copy.eventsAreBilling")} />
      {isError && <DataErrorAlert onRetry={() => void refetch()} />}
      <Timeline
        items={events.map((e) => ({
          color:
            e.to_status === "running" ? "green" : e.to_status === "failed" ? "red" : "gray",
          content: (
            <Space orientation="vertical" size={0}>
              <Typography.Text strong>
                {e.from_status ?? "—"} → {e.to_status}
                {(e.from_status === "running" || e.to_status === "running") && (
                  <Typography.Text type="secondary">{t("instances.billingBoundary")}</Typography.Text>
                )}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t("instances.eventMetaLine", {
                  time: formatDateTime(e.created_at),
                  reason: reasonText(e.reason),
                  actor: e.actor,
                })}
              </Typography.Text>
            </Space>
          ),
        }))}
      />
      {hasNextPage && (
        <Button block loading={isFetchingNextPage} onClick={() => void fetchNextPage()}>
          {t("billing.loadMore")}
        </Button>
      )}
    </Space>
  );
}

function BillsTab({ instanceId }: { instanceId: number }) {
  const { t } = useTranslation();
  const { formatDuration, formatHourlyPrice, formatMoney } = useFormat();
  // 游标分页 + 加载更多(与费用中心小时账单同构)
  const { data, isLoading, isError, refetch, hasNextPage, isFetchingNextPage, fetchNextPage } =
    useHourlyBillPages({ instance_id: instanceId });
  const rows = useMemo<BillHourlyOut[]>(() => (data?.pages ?? []).flatMap((p) => p.items), [data]);
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <Table
        rowKey="id"
        size="small"
        pagination={false}
        loading={isLoading}
        locale={{
          emptyText: isError ? <TableErrorEmpty onRetry={() => void refetch()} /> : undefined,
        }}
        dataSource={rows}
        columns={[
          { title: t("instances.colBillHour"), render: (_, r) => formatDateTime(r.hour_start) },
          { title: t("instances.colBillDuration"), render: (_, r) => formatDuration(r.seconds_used) },
          {
            title: t("instances.colBillUnit"),
            render: (_, r) => (
              <span>
                {formatHourlyPrice(r.unit_price)} × {r.gpu_count}
              </span>
            ),
          },
          {
            title: t("instances.colBillAmount"),
            render: (_, r) => <span>{formatMoney(r.amount)}</span>,
          },
        ]}
      />
      {hasNextPage && (
        <Button block loading={isFetchingNextPage} onClick={() => void fetchNextPage()}>
          {t("billing.loadMore")}
        </Button>
      )}
    </Space>
  );
}

function InstanceDetail() {
  const { t } = useTranslation();
  const { formatHourlyPrice, formatMoney, formatPeriodPrice } = useFormat();
  const { uuid } = Route.useParams();
  const { tab } = Route.useSearch();
  const navigate = useNavigate();
  const [releaseOpen, setReleaseOpen] = useState(false);
  const {
    data: instance,
    isError: instanceError,
    refetch: refetchInstance,
  } = useInstance(uuid, {
    refetchInterval: (q) =>
      q.state.data && isTransientInstanceStatus(q.state.data.status) ? 5_000 : 30_000,
  });
  const { date, tzOffsetMinutes } = localToday();
  const { data: daily } = useDailySummary(date, tzOffsetMinutes, { refetchInterval: 60_000 });
  const todayAmount =
    (instance && daily?.items.find((it) => it.instance_id === instance.id)?.total_amount) ?? null;

  if (instanceError && !instance) {
    return <DataErrorAlert onRetry={() => void refetchInstance()} />;
  }
  // 首载骨架,不留白屏
  if (!instance) {
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
  const running = instance.status === "running";
  const isService = instance.workload_type === "service";
  const canRelease = canReleaseStatus(instance.status);
  // dev 实例被人带着 ?tab=service 直接打开时回退默认 Tab,不渲染无选中态的 Tabs
  const activeTab = tab === "service" && !isService ? "metrics" : (tab ?? "metrics");

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      {/* 面包屑:列表筛选态在 URL 上,返回列表不丢 */}
      <Breadcrumb
        items={[
          {
            title: <Link to="/instances">{t("instances.title")}</Link>,
          },
          { title: instance.name },
        ]}
      />
      <Card>
        <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
          <Space orientation="vertical" size={4}>
            <Space>
              <Typography.Title level={4} style={{ margin: 0 }}>
                {instance.name}
              </Typography.Title>
              <InstanceStatusBadge
                status={instance.status}
                frozenDeadline={instance.frozen_deadline}
              />
              <TierTag tier={instance.spec["tier"] as string} pool={instance.spec["pool_label"] as string} />
              <SubscriptionTag market={instance.market} subscription={instance.subscription} />
              <SpotTag market={instance.market} />
            </Space>
            <Descriptions
              size="small"
              column={{ xs: 1, sm: 2, md: 3, xl: 4 }}
              items={[
                { label: t("instances.labelId"), children: instance.uuid.slice(0, 12) },
                {
                  label: t("instances.labelSpec"),
                  children: t("instances.specLine", { model: instance.spec["gpu_model"] as string, count: instance.gpu_count }),
                },
                {
                  label: t("instances.labelBilling"),
                  // 包周期实例的时价是折后价、且不出小时账,报「¥X/时 × N 卡」会让人以为在按小时扣
                  children: instance.subscription
                    ? formatPeriodPrice(
                        instance.subscription.amount_paid,
                        instance.subscription.period,
                        instance.subscription.period_count,
                      )
                    : t("instances.pricePerCard", { price: formatHourlyPrice(instance.price_hourly), count: instance.gpu_count }),
                },
                ...(instance.subscription
                  ? [
                      {
                        label: t("instances.labelExpiresAt"),
                        children: formatDateTime(instance.subscription.expires_at),
                      },
                    ]
                  : [
                      {
                        label: t("instances.labelToday"),
                        children: moneyOr(formatMoney(todayAmount), daily != null),
                      },
                    ]),
                { label: t("instances.createdAt"), children: formatDateTime(instance.created_at) },
              ]}
            />
          </Space>
          {/* 详情页的「事件日志」跳到本页事件 Tab(不传 onShowEvents 即死按钮) */}
          <InstanceActions
            instance={instance}
            onShowEvents={() =>
              void navigate({ to: "/instances/$uuid", params: { uuid }, search: { tab: "events" } })
            }
          />
        </Space>
      </Card>

      <Tabs
        activeKey={activeTab}
        onChange={(k) =>
          navigate({ to: "/instances/$uuid", params: { uuid }, search: { tab: k } })
        }
        items={[
          {
            key: "metrics",
            label: t("instances.tabMetrics"),
            children: <MetricsTab uuid={uuid} running={running} />,
          },
          // 「服务」只对服务型实例出:开发机没有端点也没有 Key
          ...(isService
            ? [
                {
                  key: "service",
                  label: t("instances.tabService"),
                  children: (
                    <ServiceTab
                      instance={instance}
                      onShowLogs={() =>
                        void navigate({
                          to: "/instances/$uuid",
                          params: { uuid },
                          search: { tab: "logs" },
                        })
                      }
                    />
                  ),
                },
              ]
            : []),
          {
            key: "access",
            label: t("instances.tabAccess"),
            children: <AccessTab instance={instance} running={running} />,
          },
          {
            key: "logs",
            label: t("instances.tabLogs"),
            children: (
              <LogsTab
                uuid={uuid}
                viewable={instance.status === "running" || instance.status === "stopping"}
              />
            ),
          },
          {
            key: "events",
            label: t("instances.tabEvents"),
            children: <EventsTab uuid={uuid} status={instance?.status} />,
          },
          { key: "bills", label: t("instances.tabBills"), children: <BillsTab instanceId={instance.id} /> },
        ]}
      />

      <Card title={t("instances.dangerZone")} style={{ borderColor: "#ffccc7" }}>
        <Space orientation="vertical">
          <Typography.Text type="secondary">
            {t("instances.dangerNote")}
          </Typography.Text>
          <Button
            danger
            disabled={!canRelease}
            title={canRelease ? undefined : t("copy.releaseNeedsStopped")}
            onClick={() => setReleaseOpen(true)}
          >
            {t("instances.release")}
          </Button>
        </Space>
      </Card>
      <ReleaseModal
        instance={instance}
        open={releaseOpen}
        onClose={() => setReleaseOpen(false)}
        onReleased={() => navigate({ to: "/instances" })}
      />
    </Space>
  );
}
