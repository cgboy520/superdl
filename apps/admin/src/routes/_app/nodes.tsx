import {
  adminColors,
  fontSize,
  formatDateTime,
  heatColors,
  layout,
  metaOf,
  nodeEnrollStatusMap,
  POLL,
  space,
  textOnAccent,
  useAutoRefresh,
  type NodeEnrollStatus,
} from "@superdl/ui";
import { EChart, PageContainer, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import {
  Alert,
  App,
  Radio,
  Badge,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  theme,
} from "antd";
import dayjs from "dayjs";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type EnrollmentCommandOut,
  type EnrollmentRow,
  type NodeMetricsOut,
  type NodeRow,
  isApiError,
  useNodeMetrics,
  useCordonNode,
  useCreateEnrollment,
  useEnrollments,
  useNodes,
  usePortPool,
  useRegenerateEnrollment,
  useRevokeEnrollment,
} from "../../api";
import { useApiErrorText } from "@superdl/ui";
import { useFormDraft } from "@superdl/ui";
import { POOL_LABEL_KEY } from "../../lib/pools";
import { ReasonAction } from "../../components/ReasonAction";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/nodes")({
  // node:告警深链(/nodes?node=<name>)目标行
  validateSearch: (search: Record<string, unknown>): { node?: string } => ({
    node: typeof search.node === "string" && search.node ? search.node : undefined,
  }),
  component: NodesPage,
});

const PHASE_LABEL = {
  bootstrap: "nodes.phase.bootstrap",
  precheck: "nodes.phase.precheck",
  nouveau: "nodes.phase.nouveau",
  sysctl: "nodes.phase.sysctl",
  iommu: "nodes.phase.iommu",
  driver: "nodes.phase.driver",
  nvidia_toolkit: "nodes.phase.nvidiaToolkit",
  nvme_vg: "nodes.phase.nvmeVg",
  reboot: "nodes.phase.reboot",
  registries: "nodes.phase.registries",
  agent_config: "nodes.phase.agentConfig",
  agent_install: "nodes.phase.agentInstall",
  agent_start: "nodes.phase.agentStart",
  waiting_node: "nodes.phase.waitingNode",
  joined: "nodes.phase.joined",
} as const;

const HEAT_COLORS = { idle: adminColors.gridLine, ...heatColors };

const HEAT_LEGEND_KEY = {
  idle: "nodes.heatLegend.idle",
  low: "nodes.heatLegend.low",
  mid: "nodes.heatLegend.mid",
  high: "nodes.heatLegend.high",
} as const;

function heatColor(util: number): string {
  if (util < 10) return HEAT_COLORS.idle;
  if (util < 60) return HEAT_COLORS.low;
  if (util < 85) return HEAT_COLORS.mid;
  return HEAT_COLORS.high;
}

function last(points?: [number, number][] | null): number | null {
  const p = points?.[points.length - 1];
  return p ? p[1] : null;
}

/** 每卡热力格:有指标按 util 染色(tooltip 给 util/显存/温度);断源回落「已租/空闲」两态,断源空闲格斜纹底。 */
function GpuGrid({ node, metrics }: { node: NodeRow; metrics: NodeMetricsOut | undefined }) {
  const { t } = useTranslation();
  const byIndex = new Map((metrics?.gpus ?? []).map((g) => [String(g.index), g]));
  const live = Boolean(metrics?.available && byIndex.size > 0);
  return (
    <>
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      {Array.from({ length: node.gpu_total }, (_, i) => {
        const g = byIndex.get(String(i));
        const util = live ? last(g?.util) : null;
        const mem = last(g?.mem_used_mb);
        const temp = last(g?.temp);
        const used = i < node.gpu_used;
        const title = live
          ? t("nodes.gpuCellLive", {
              index: i,
              util: util == null ? "—" : Math.round(util),
              mem: mem == null ? "—" : Math.round(mem / 1024),
              temp: temp == null ? "—" : Math.round(temp),
            })
          : used
            ? t("nodes.gpuCellUsed", { index: i })
            : t("nodes.gpuCellFree", { index: i });
        const bg = live
          ? heatColor(util ?? 0)
          : used
            ? adminColors.dataAccent
            : `repeating-linear-gradient(135deg, ${adminColors.gridLine} 0 6px, transparent 6px 12px)`;
        return (
          <Tooltip key={i} title={title}>
            <div
              role="img"
              aria-label={title}
              style={{
                width: 52,
                height: 44,
                borderRadius: 6,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                fontSize: fontSize.caption,
                lineHeight: 1.2,
                background: bg,
                // 字色随底:深底浅字,亮青(断源已租)深字
                color: !live
                  ? used
                    ? adminColors.bgBase
                    : adminColors.textSecondary
                  : (util ?? 0) >= 10
                    ? textOnAccent
                    : adminColors.textSecondary,
                fontWeight: 600,
              }}
            >
              <span>{i}</span>
              {live && <span>{util == null ? "—" : `${Math.round(util)}%`}</span>}
            </div>
          </Tooltip>
        );
      })}
    </div>
    {/* 色阶图例:四档 + 断源两态 */}
    <Space size={12} wrap style={{ marginTop: 12 }}>
      {(Object.keys(HEAT_LEGEND_KEY) as (keyof typeof HEAT_LEGEND_KEY)[]).map((key) => (
        <Space key={key} size={4}>
          <span style={{ display: "inline-block", width: 12, height: 12, borderRadius: 3, background: HEAT_COLORS[key] }} />
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t(HEAT_LEGEND_KEY[key])}
          </Typography.Text>
        </Space>
      ))}
      <Space size={4}>
        <span
          style={{
            display: "inline-block",
            width: 12,
            height: 12,
            borderRadius: 3,
            background: `repeating-linear-gradient(135deg, ${adminColors.gridLine} 0 4px, transparent 4px 8px)`,
            border: `1px solid ${adminColors.gridLine}`,
          }}
        />
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("nodes.heatLegend.offlineFree")}
        </Typography.Text>
      </Space>
      <Space size={4}>
        <span style={{ display: "inline-block", width: 12, height: 12, borderRadius: 3, background: adminColors.dataAccent }} />
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("nodes.heatLegend.offlineUsed")}
        </Typography.Text>
      </Space>
    </Space>
    </>
  );
}

/** 节点级历史曲线(per-GPU util / 显存)+ XID 徽标 + 可选 Grafana 外链。 */
function NodeMetricsPanel({
  node,
  metrics,
  range,
  onRangeChange,
}: {
  node: NodeRow;
  metrics: NodeMetricsOut | undefined;
  range: string;
  onRangeChange: (r: string) => void;
}) {
  const { t } = useTranslation();
  const gpus = metrics?.gpus ?? [];
  const chart = (key: "util" | "mem_used_mb", title: string, unit: string) => (
    <Card size="small" title={title}>
      <EChart
        style={{ height: 200 }}
        theme="noc"
        ariaLabel={title}
        option={{
          grid: { left: 48, right: 16, top: 28, bottom: 24 },
          legend: { top: 0, textStyle: { fontSize: fontSize.caption } },
          xAxis: { type: "time" },
          yAxis: { type: "value", axisLabel: { formatter: `{value}${unit}` } },
          tooltip: { trigger: "axis" },
          series: gpus.map((g) => ({
            name: `GPU ${g.index}`,
            type: "line",
            showSymbol: false,
            data: (g[key] ?? []).map(([ts, v]) => [ts * 1000, v]),
          })),
        }}
      />
    </Card>
  );
  const xid = metrics?.xid_count_24h ?? 0;
  const grafanaUrl = metrics?.grafana_url;
  return (
    <Card
      title={t("nodes.historyTitle")}
      style={{ marginTop: 16 }}
      extra={
        <Space size={12}>
          {xid > 0 && <Tag color="red">{t("nodes.xidBadge", { count: xid })}</Tag>}
          <Radio.Group
            size="small"
            value={range}
            onChange={(e) => onRangeChange(e.target.value as string)}
            optionType="button"
            options={[
              { value: "1h", label: t("nodes.range1h") },
              { value: "6h", label: t("nodes.range6h") },
              { value: "24h", label: t("nodes.range24h") },
            ]}
          />
          {grafanaUrl && (
            <Button
              size="small"
              onClick={() => window.open(grafanaUrl, "_blank", "noopener,noreferrer")}
            >
              {t("nodes.openGrafana")}
            </Button>
          )}
        </Space>
      }
    >
      {metrics?.available && gpus.length > 0 ? (
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          {chart("util", t("nodes.utilChart"), "%")}
          {chart("mem_used_mb", t("nodes.vramChart"), "MB")}
        </Space>
      ) : (
        <Typography.Text type="secondary">
          {t("nodes.historyPending")} · {node.name}
        </Typography.Text>
      )}
    </Card>
  );
}

/** 命令展示(创建/重新生成共用):令牌只显示这一次 */
function CommandPanel({ result }: { result: EnrollmentCommandOut }) {
  const { t } = useTranslation();
  return (
    <Space orientation="vertical" size={12} style={{ width: "100%" }}>
      <Alert
        type="warning"
        showIcon
        title={t("nodes.tokenOnce")}
        description={t("nodes.tokenOnceDesc", { time: formatDateTime(result.enrollment.expires_at) })}
      />
      <div>
        <Typography.Text type="secondary">{t("nodes.cmdPiped")}</Typography.Text>
        <Typography.Paragraph copyable code style={{ marginBottom: 8 }}>
          {result.curl_command}
        </Typography.Paragraph>
        <Typography.Text type="secondary">{t("nodes.cmdCautious")}</Typography.Text>
        <Typography.Paragraph copyable code style={{ marginBottom: 0 }}>
          {result.wget_command}
        </Typography.Paragraph>
      </div>
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {t("nodes.cmdFootnote")}
      </Typography.Text>
    </Space>
  );
}

interface EnrollFormValues {
  pool: "kata" | "hami" | "mig";
  hostname: string;
  note?: string;
  nvme_devices?: string[];
  ttl_hours: number;
}

// 与后端 nodes/schemas.py HOSTNAME_PATTERN 对齐
const HOSTNAME_PATTERN =
  /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/;

function AddNodeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const [form] = Form.useForm<EnrollFormValues>();
  // 新建草稿(sessionStorage),生成命令成功后清除
  const draft = useFormDraft<EnrollFormValues>("node-new");
  const [result, setResult] = useState<EnrollmentCommandOut | null>(null);
  const [idemKey, setIdemKey] = useState(() => crypto.randomUUID());
  const create = useCreateEnrollment({
    mutation: {
      onSuccess: (r) => {
        draft.clear();
        setResult(r);
      },
      onError: (e) => message.error(errText(e, t("nodes.generateFailed"))),
    },
  });

  const close = () => {
    setResult(null);
    form.resetFields();
    // 幂等键随下一次注册轮换
    setIdemKey(crypto.randomUUID());
    onClose();
  };

  return (
    <Modal
      title={result ? t("nodes.cmdModalTitle") : t("nodes.addNode")}
      open={open}
      onCancel={close}
      footer={
        result ? (
          <Button type="primary" onClick={close}>
            {t("nodes.done")}
          </Button>
        ) : (
          <Button
            type="primary"
            loading={create.isPending}
            onClick={async () => {
              try {
                const values = await form.validateFields();
                create.mutate({ data: values, idempotencyKey: idemKey });
              } catch {
                // 校验失败:antd 已给红字
              }
            }}
          >
            {t("nodes.generateCmd")}
          </Button>
        )
      }
      width="min(640px, 100vw)"
      destroyOnHidden
    >
      {result ? (
        <CommandPanel result={result} />
      ) : (
        <Form form={form} layout="vertical" initialValues={{ pool: "hami", ttl_hours: 24 }}>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            title={t("nodes.poolRule")}
            description={t("nodes.poolRuleDesc")}
          />
          <Form.Item name="pool" label={t("nodes.poolLabel")} rules={[{ required: true }]}>
            <Select
              options={Object.entries(POOL_LABEL_KEY).map(([value, labelKey]) => ({ value, label: t(labelKey) }))}
            />
          </Form.Item>
          <Form.Item
            name="hostname"
            label={t("nodes.hostnameLabel")}
            rules={[{ required: true }, { pattern: HOSTNAME_PATTERN }]}
          >
            <Input placeholder={t("nodes.hostnamePlaceholder")} />
          </Form.Item>
          <Form.Item name="note" label={t("nodes.noteLabel")}>
            <Input placeholder={t("nodes.notePlaceholder")} maxLength={128} />
          </Form.Item>
          <Form.Item
            name="nvme_devices"
            label={t("nodes.nvmeLabel")}
            extra={t("nodes.nvmeExtra")}
          >
            <Select mode="tags" placeholder={t("nodes.nvmePlaceholder")} open={false} />
          </Form.Item>
          <Form.Item name="ttl_hours" label={t("nodes.ttlLabel")} rules={[{ required: true }]}>
            <InputNumber min={1} max={168} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      )}
    </Modal>
  );
}

function EnrollmentsCard({ writable }: { writable: boolean }) {
  const { t } = useTranslation(["admin", "shared"]);
  const qc = useQueryClient();
  // 进行中 = 活跃行(5s 轮询);全部 = 含 joined/expired/revoked
  const [scope, setScope] = useState<"active" | "all">("active");
  const { data, queryKey, isLoading, isError, error, refetch } = useEnrollments({
    active: scope === "active" ? true : undefined,
    refetchInterval: scope === "active" ? 5_000 : undefined,
  });
  const rows: EnrollmentRow[] = data ?? [];
  const [regenResult, setRegenResult] = useState<EnrollmentCommandOut | null>(null);
  // 错误提示统一由 ReasonAction 弹出
  const regenerate = useRegenerateEnrollment({
    mutation: {
      onSuccess: (r) => {
        setRegenResult(r);
        void qc.invalidateQueries({ queryKey });
      },
    },
  });
  const revoke = useRevokeEnrollment({
    mutation: { onSuccess: () => void qc.invalidateQueries({ queryKey }) },
  });

  // 无数据照常渲染空态;查询失败由表内空态明示
  return (
    <Card
      title={scope === "active" ? t("nodes.pendingTitle") : t("nodes.allEnrollmentsTitle")}
      style={{ marginBottom: 16 }}
      extra={
        <Radio.Group
          size="small"
          optionType="button"
          value={scope}
          onChange={(e) => setScope(e.target.value as "active" | "all")}
          options={[
            { value: "active", label: t("nodes.scopeActive") },
            { value: "all", label: t("nodes.scopeAll") },
          ]}
        />
      }
    >      <Table<EnrollmentRow>
        size="small"
        rowKey="id"
        scroll={{ x: 900 }}
        loading={isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={isError}
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            >
              {scope === "active" ? t("nodes.noActiveEnrollments") : undefined}
            </TableErrorEmpty>
          ),
        }}
        dataSource={rows}
        pagination={false}
        columns={[
          {
            title: t("nodes.colPool"),
            dataIndex: "pool",
            render: (v: string) => <Tag color="cyan">{v}</Tag>,
          },
          {
            title: t("nodes.colHostname"),
            render: (_, r) => r.node_name ?? r.hostname ?? "-",
          },
          { title: t("nodes.noteCol"), dataIndex: "note", render: (v: string | null) => v ?? "-" },
          {
            title: t("nodes.colCreatedAt"),
            dataIndex: "created_at",
            width: 150,
            render: formatDateTime,
            sorter: (a, b) => dayjs(a.created_at).valueOf() - dayjs(b.created_at).valueOf(),
          },
          {
            title: t("nodes.colStatus"),
            dataIndex: "status",
            render: (v: NodeEnrollStatus) => {
              const meta = metaOf(nodeEnrollStatusMap, v);
              return meta ? <Badge status={meta.badge} text={t(meta.labelKey)} /> : v;
            },
          },
          {
            title: t("nodes.colPhase"),
            dataIndex: "phase",
            render: (v: string | null) => {
              const key = v ? metaOf(PHASE_LABEL, v) : undefined;
              return key ? t(key) : (v ?? "-");
            },
          },
          {
            title: t("nodes.colHeartbeat"),
            dataIndex: "last_report_at",
            // 秒级精度(formatDateTime 只到分)
            render: (v: string | null) => (v ? dayjs(v).format("MM-DD HH:mm:ss") : "-"),
          },
          {
            title: t("nodes.colError"),
            dataIndex: "error",
            width: 240,
            render: (v: string | null) =>
              v ? (
                <Tooltip title={v}>
                  <Typography.Text type="danger" ellipsis style={{ maxWidth: 220 }}>
                    {v}
                  </Typography.Text>
                </Tooltip>
              ) : (
                "-"
              ),
          },
          {
            title: t("nodes.colActions"),
            width: 190,
            render: (_, r) => (
              <Space>
                {/* 重新生成:旧命令立即失效,先收原因 */}
                <ReasonAction
                  label={t("nodes.regenerate")}
                  target={r.hostname ?? `#${r.id}`}
                  title={t("nodes.regenerateConfirmTitle")}
                  confirmText={t("nodes.regenerateConfirmDesc", { host: r.hostname ?? `#${r.id}` })}
                  disabled={!writable || !["pending", "expired", "failed"].includes(r.status)}
                  disabledReason={
                    !writable ? t("nodes.readonlyNoOp") : t("nodes.regenerateOnly")
                  }
                  onSubmit={async (reason) => {
                    await regenerate.mutateAsync({ enrollmentId: r.id, data: { reason } });
                  }}
                />
                {!["joined", "failed", "expired", "revoked"].includes(r.status) && (
                  <ReasonAction
                    label={t("nodes.revoke")}
                    target={r.hostname ?? `#${r.id}`}
                    title={t("nodes.revokeTitle")}
                    confirmText={t("nodes.revokeConfirm", { host: r.hostname ?? `#${r.id}` })}
                    danger
                    disabled={!writable}
                    disabledReason={t("nodes.readonlyNoRevoke")}
                    onSubmit={async (reason) => {
                      await revoke.mutateAsync({ enrollmentId: r.id, data: { reason } });
                    }}
                  />
                )}
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title={t("nodes.newCmdTitle")}
        open={regenResult !== null}
        onCancel={() => setRegenResult(null)}
        footer={
          <Button type="primary" onClick={() => setRegenResult(null)}>
            {t("nodes.done")}
          </Button>
        }
        width="min(640px, 100vw)"
      >
        {regenResult && <CommandPanel result={regenResult} />}
      </Modal>
    </Card>
  );
}

function NodesPage() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const { token } = theme.useToken();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  // 节点台账稳态轮询(可暂停),页头出新鲜度条
  const autoRefresh = useAutoRefresh(POLL.steady);
  const {
    data,
    dataUpdatedAt: nodesUpdatedAt,
    isLoading,
    isError,
    isRefetching,
    error,
    refetch,
  } = useNodes({ refetchInterval: autoRefresh.refetchInterval });
  const nodes: NodeRow[] = useMemo(() => data ?? [], [data]);
  const { data: portPool } = usePortPool();
  const nodeParam = Route.useSearch({ select: (s) => s.node });
  const [selected, setSelected] = useState<string | null>(null);
  const [range, setRange] = useState("1h");
  const [addOpen, setAddOpen] = useState(false);
  // 节点名过滤(commit 制,客户端过滤全量小表)
  const [kwInput, setKwInput] = useState("");
  const [kw, setKw] = useState("");
  const filteredNodes = useMemo(
    () => (kw ? nodes.filter((n) => n.name.toLowerCase().includes(kw)) : nodes),
    [nodes, kw],
  );
  // ?node= 目标不存在时顶部提示
  const deepLinkMissing =
    nodeParam !== undefined && data !== undefined && !nodes.some((n) => n.name === nodeParam);
  const node =
    deepLinkMissing && selected === nodeParam
      ? undefined
      : (nodes.find((n) => n.name === selected) ?? nodes[0]);
  const { data: nodeMetrics } = useNodeMetrics(node?.name ?? null, range);
  // 告警深链:选中目标行并滚动到可视区(data-row-key 定位;渲染期派生态)
  const [prevNodeParam, setPrevNodeParam] = useState(nodeParam);
  if (nodeParam !== prevNodeParam) {
    setPrevNodeParam(nodeParam);
    if (nodeParam) setSelected(nodeParam);
  }
  useEffect(() => {
    if (!nodeParam || nodes.length === 0) return;
    const row = document.querySelector(`[data-row-key="${CSS.escape(nodeParam)}"]`);
    row?.scrollIntoView({ block: "center" });
  }, [nodeParam, nodes.length]);
  // cordon 经 outbox 异步生效,3s 后补拉一次
  const cordonTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (cordonTimer.current) clearTimeout(cordonTimer.current);
    },
    [],
  );
  const cordon = useCordonNode({
    mutation: {
      onSuccess: (r, v) => {
        message.success(
          t("nodes.cordonSubmitted", {
            action: v.on ? t("nodes.actionCordon") : t("nodes.actionUncordon"),
          }),
        );
        void qc.invalidateQueries({ queryKey: ["admin", "nodes"] });
        if ((r as { queued?: boolean }).queued) {
          cordonTimer.current = setTimeout(
            () => void qc.invalidateQueries({ queryKey: ["admin", "nodes"] }),
            3_000,
          );
        }
      },
      onError: (e, v) =>
        message.error(
          errText(e, t("common.actionFailed", {
            action: v.on ? t("nodes.actionCordon") : t("nodes.actionUncordon"),
          })),
        ),
    },
  });
  const poolFilters = [...new Set(nodes.map((n) => (n.unlabeled ? "" : n.pool_label)))].map((p) =>
    p ? { text: p, value: p } : { text: t("nodes.unlabeledTag"), value: "" },
  );

  return (
    <PageContainer
      width="full"
      title={t("nodes.title")}
      freshness={{
        updatedAt: nodesUpdatedAt,
        intervalMs: autoRefresh.intervalMs,
        paused: autoRefresh.paused,
        onTogglePause: autoRefresh.toggle,
        onRefresh: () => void refetch(),
        refreshing: isRefetching,
      }}
      extra={
        <Space size={12}>
          {portPool && (
            <Tooltip title={t("nodes.portPoolHint")}>
              <Tag
                color={portPool.blocked > 0 ? "red" : "default"}
                style={{ marginInlineEnd: 0 }}
              >
                {t("nodes.portPool", {
                  assigned: portPool.assigned,
                  total: portPool.total,
                  blocked: portPool.blocked,
                })}
              </Tag>
            </Tooltip>
          )}
          <Tooltip title={writable ? "" : t("nodes.readonlyNoAdd")}>
            <Button type="primary" disabled={!writable} onClick={() => setAddOpen(true)}>
              {t("nodes.addNode")}
            </Button>
          </Tooltip>
        </Space>
      }
    >
      {deepLinkMissing && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: space.lg }}
          title={t("nodes.deepLinkMissing", { name: nodeParam })}
        />
      )}
      <EnrollmentsCard writable={writable} />
      <Card>
        <Input.Search
          allowClear
          placeholder={t("nodes.searchPlaceholder")}
          style={{ width: 240, marginBottom: space.md }}
          value={kwInput}
          onChange={(e) => {
            setKwInput(e.target.value);
            // 清空立即提交
            if (e.target.value === "") setKw("");
          }}
          onSearch={(v) => setKw(v.trim().toLowerCase())}
        />
        <Table<NodeRow>
          scroll={{ x: 1200 }}
          sticky={{ offsetHeader: layout.topBarHeight }}
          rowKey="name"
          loading={isLoading}
          locale={{
            emptyText: kw ? (
              t("nodes.noMatch")
            ) : (
              <TableErrorEmpty
                isError={isError}
                isForbidden={isApiError(error) && error.status === 403}
                onRetry={() => void refetch()}
              />
            ),
          }}
          dataSource={filteredNodes}
          // >200 行改 100/页分页,不上虚拟化
          pagination={
            filteredNodes.length > 200
              ? { pageSize: 100, showSizeChanger: false, hideOnSinglePage: true }
              : false
          }
          onRow={(r) => ({
            onClick: () => setSelected(r.name),
            // 整行即按钮(Enter/Space 选中)
            tabIndex: 0,
            onKeyDown: (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setSelected(r.name);
              }
            },
            style: {
              cursor: "pointer",
              ...(r.name === node?.name
                ? { background: token.colorPrimaryBg, boxShadow: `inset 0 0 0 1px ${token.colorPrimary}` }
                : {}),
            },
          })}
          columns={[
            {
              title: t("nodes.colNode"),
              dataIndex: "name",
              fixed: "left",
              width: 180,
              sorter: (a, b) => a.name.localeCompare(b.name),
              render: (v: string) => <span className="mono">{v}</span>,
            },
            {
              title: t("nodes.colPool"),
              dataIndex: "pool_label",
              filters: poolFilters,
              onFilter: (v, r) => (r.unlabeled ? "" : r.pool_label) === v,
              render: (v: string, r) =>
                r.unlabeled || !v ? (
                  <Tag color="red">{t("nodes.unlabeledTag")}</Tag>
                ) : (
                  <Tag color="cyan">{v}</Tag>
                ),
            },
            {
              title: t("nodes.colGpu"),
              render: (_, r) => {
                const unrecognized = r.gpu_model === "GPU" && !!r.gpu_model_raw;
                return (
                  <Space size={4}>
                    <span>{`${unrecognized ? r.gpu_model_raw : r.gpu_model} × ${r.gpu_total}`}</span>
                    {unrecognized && <Tag color="gold">{t("nodes.unrecognizedTag")}</Tag>}
                    {!unrecognized && r.gpu_model !== "GPU" && r.label_synced === false && (
                      <Tooltip title={t("nodes.labelUnsynced")}>
                        <Tag color="orange">!</Tag>
                      </Tooltip>
                    )}
                  </Space>
                );
              },
            },
            {
              title: t("nodes.colVram"),
              render: (_, r) => (r.vram_gb ? `${r.vram_gb} G` : "—"),
            },
            {
              title: t("nodes.colUsed"),
              dataIndex: "gpu_used",
              sorter: (a, b) => a.gpu_used - b.gpu_used,
              // 已用卡数链到「租户与实例 › 实例」按节点过滤:从节点直达上面跑着谁
              render: (v: number, r) =>
                v > 0 ? (
                  <Link to="/tenants" search={{ tab: "instances", inode: r.name }}>
                    {v}
                  </Link>
                ) : (
                  v
                ),
            },
            { title: t("nodes.colDriver"), render: (_, r) => r.driver_version || "—" },
            { title: "CUDA", render: (_, r) => r.cuda_version || "—" },
            { title: t("nodes.colCpu"), render: (_, r) => t("nodes.coreCount", { count: r.vcpu }) },
            {
              title: t("nodes.colMem"),
              render: (_, r) => `${r.mem_gb} G`,
              sorter: (a, b) => a.mem_gb - b.mem_gb,
            },
            {
              title: t("nodes.colDisk"),
              render: (_, r) => `${r.disk_gb} G`,
              sorter: (a, b) => a.disk_gb - b.disk_gb,
            },
            {
              title: t("nodes.colLastSeen"),
              dataIndex: "last_seen",
              width: 130,
              sorter: (a, b) => dayjs(a.last_seen || 0).valueOf() - dayjs(b.last_seen || 0).valueOf(),
              // 相对时间,hover 给绝对时间;空 = 尚无台账行
              render: (v: string) =>
                v ? (
                  <Tooltip title={formatDateTime(v)}>
                    <span>{dayjs(v).fromNow()}</span>
                  </Tooltip>
                ) : (
                  "—"
                ),
            },
            {
              title: t("nodes.colStatus"),
              dataIndex: "status",
              filters: ["Ready", "NotReady", "Cordoned", "Missing"].map((s) => ({
                text: s,
                value: s,
              })),
              onFilter: (v, r) => r.status === v,
              render: (v: string) => (
                <Tag
                  color={
                    v === "Ready"
                      ? "green"
                      : v === "Cordoned"
                        ? "orange"
                        : v === "Missing"
                          ? "default"
                          : "red"
                  }
                >
                  {v}
                </Tag>
              ),
            },
            {
              title: t("nodes.colActions"),
              width: 170,
              fixed: "right",
              render: (_, r) => {
                const cordoned = r.status === "Cordoned";
                return (
                  <Space>
                    <ReasonAction
                      label={cordoned ? t("nodes.uncordonBtn") : t("nodes.cordonBtn")}
                      target={r.name}
                      title={cordoned ? t("nodes.uncordonTitle") : t("nodes.cordonTitle")}
                      confirmText={
                        cordoned
                          ? t("nodes.uncordonConfirm", { name: r.name })
                          : t("nodes.cordonConfirm", { name: r.name })
                      }
                      danger={!cordoned}
                      disabled={!writable}
                      disabledReason={t("nodes.readonlyNoOp")}
                      onSubmit={async (reason) => {
                        await cordon.mutateAsync({
                          nodeName: r.name,
                          on: !cordoned,
                          data: { reason },
                        });
                      }}
                    />
                    {/* 占位项:可见但禁用 + tooltip */}
                    <Tooltip title={t("nodes.drainDeferred")}>
                      <Button size="small" disabled>
                        {t("nodes.drainBtn")}
                      </Button>
                    </Tooltip>
                  </Space>
                );
              },
            },
          ]}
        />
      </Card>
      <AddNodeModal open={addOpen} onClose={() => setAddOpen(false)} />
      {node && (
        <>
          <Card title={t("nodes.gpuGridTitle", { name: node.name })} style={{ marginTop: 16 }}>
            <GpuGrid node={node} metrics={nodeMetrics} />
          </Card>
          <NodeMetricsPanel
            node={node}
            metrics={nodeMetrics}
            range={range}
            onRangeChange={setRange}
          />
        </>
      )}
    </PageContainer>
  );
}
