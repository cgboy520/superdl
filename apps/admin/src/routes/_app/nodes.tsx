import { adminColors, formatDateTime, heatColors, metaOf, nodeEnrollStatusMap, type NodeEnrollStatus } from "@superdl/ui";
import { TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
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
} from "antd";
import dayjs from "dayjs";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import EChart from "../../components/EChart";
import {
  type EnrollmentCommandOut,
  type EnrollmentRow,
  type NodeMetricsOut,
  type NodeRow,
  useNodeMetrics,
  useCordonNode,
  useCreateEnrollment,
  useEnrollments,
  useNodes,
  usePortPool,
  useRegenerateEnrollment,
  useRevokeEnrollment,
} from "../../api";
import { useApiErrorText } from "../../lib/apiError";
import { useFormDraft } from "../../lib/formDraft";
import { POOL_LABEL_KEY } from "../../lib/pools";
import { ReasonAction } from "../../components/ReasonAction";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/nodes")({
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

// 热力格深底浅字(WCAG AA):取值收敛在 packages/ui heatColors,白字对比度 ≥4.5:1
const HEAT_COLORS = { idle: adminColors.gridLine, ...heatColors };

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

/** 每卡热力格:有指标时按 util 染色(tooltip 给 util/显存/温度);断源回落「已租/空闲」两态。 */
function GpuGrid({ node, metrics }: { node: NodeRow; metrics: NodeMetricsOut | undefined }) {
  const { t } = useTranslation();
  const byIndex = new Map((metrics?.gpus ?? []).map((g) => [String(g.index), g]));
  const live = Boolean(metrics?.available && byIndex.size > 0);
  return (
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
            : adminColors.gridLine;
        return (
          <Tooltip key={i} title={title}>
            <div
              style={{
                width: 52,
                height: 44,
                borderRadius: 6,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 11,
                lineHeight: 1.2,
                background: bg,
                // 字色随底:深底(热力档/空闲格)浅字;亮青(断源已租)反压深字;断源空闲格深底浅字
                color: !live
                  ? used
                    ? adminColors.bgBase
                    : adminColors.textSecondary
                  : (util ?? 0) >= 10
                    ? "#fff"
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
        option={{
          grid: { left: 48, right: 16, top: 28, bottom: 24 },
          legend: { top: 0, textStyle: { fontSize: 11 } },
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
        description={t("nodes.tokenOnceDesc", { time: dayjs(result.enrollment.expires_at).format("MM-DD HH:mm") })}
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
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
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

// 与后端 nodes/schemas.py HOSTNAME_PATTERN 对齐(签发时强制绑定主机名,防令牌串用)
const HOSTNAME_PATTERN =
  /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/;

function AddNodeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const [form] = Form.useForm<EnrollFormValues>();
  // 新建草稿(sessionStorage):误关弹窗不丢;生成命令成功后清除
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
    // 幂等键必须随「下一次注册」轮换:不换会让本页注册的第二个节点拿到第一个节点的加入命令
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
              const values = await form.validateFields();
              create.mutate({ data: values, idempotencyKey: idemKey });
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
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const qc = useQueryClient();
  // 进行中=活跃行(5s 轮询);全部=含 joined/expired/revoked 的历史装机记录
  const [scope, setScope] = useState<"active" | "all">("active");
  const { data, queryKey, isLoading, isError, refetch } = useEnrollments({
    active: scope === "active" ? true : undefined,
    refetchInterval: scope === "active" ? 5_000 : undefined,
  });
  const rows: EnrollmentRow[] = data ?? [];
  const [regenResult, setRegenResult] = useState<EnrollmentCommandOut | null>(null);
  const regenerate = useRegenerateEnrollment({
    mutation: {
      onSuccess: (r) => {
        setRegenResult(r);
        void qc.invalidateQueries({ queryKey });
      },
      onError: (e) => message.error(errText(e, t("nodes.regenerateFailed"))),
    },
  });
  const revoke = useRevokeEnrollment({
    mutation: { onSuccess: () => void qc.invalidateQueries({ queryKey }) },
  });

  // 查询失败也要露出(进行中注册被误判为「没有」会误导装机值班),错误态由表内空态明示
  if (rows.length === 0 && scope === "active" && !isError) return null;
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
          emptyText: <TableErrorEmpty isError={isError} onRetry={() => void refetch()} />,
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
                <Tooltip
                  title={
                    !writable
                      ? t("nodes.readonlyNoOp")
                      : ["pending", "expired", "failed"].includes(r.status)
                        ? t("nodes.regenerateTip")
                        : t("nodes.regenerateOnly")
                  }
                >
                  <Button
                    size="small"
                    disabled={!writable || !["pending", "expired", "failed"].includes(r.status)}
                    loading={regenerate.isPending && regenerate.variables?.enrollmentId === r.id}
                    onClick={() => regenerate.mutate({ enrollmentId: r.id, data: {} })}
                  >
                    {t("nodes.regenerate")}
                  </Button>
                </Tooltip>
                {!["joined", "failed", "expired", "revoked"].includes(r.status) && (
                  <ReasonAction
                    label={t("nodes.revoke")}
                    title={t("nodes.revokeTitle")}
                    confirmText={t("nodes.revokeConfirm")}
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
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data, isLoading, isError, refetch } = useNodes();
  const nodes: NodeRow[] = data ?? [];
  const { data: portPool } = usePortPool();
  const [selected, setSelected] = useState<string | null>(null);
  const [range, setRange] = useState("1h");
  const [addOpen, setAddOpen] = useState(false);
  const node = nodes.find((n) => n.name === selected) ?? nodes[0];
  const { data: nodeMetrics } = useNodeMetrics(node?.name ?? null, range);
  // cordon 经 outbox 异步生效:3s 后补拉一次;组件卸载必须清定时器
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
        message.success(t("nodes.cordonSubmitted", { action: v.on ? "cordon" : "uncordon" }));
        void qc.invalidateQueries({ queryKey: ["admin", "nodes"] });
        // queued=true:经 outbox 异步执行,3s 后补拉一次看生效
        if ((r as { queued?: boolean }).queued) {
          cordonTimer.current = setTimeout(
            () => void qc.invalidateQueries({ queryKey: ["admin", "nodes"] }),
            3_000,
          );
        }
      },
      onError: (e) => message.error(errText(e, t("common.actionFailed", { action: "" }))),
    },
  });
  const poolFilters = [...new Set(nodes.map((n) => (n.unlabeled ? "" : n.pool_label)))].map((p) =>
    p ? { text: p, value: p } : { text: t("nodes.unlabeledTag"), value: "" },
  );

  return (
    <>
      <EnrollmentsCard writable={writable} />
      <Card
        title={t("nodes.title")}
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
        <Table<NodeRow>
          scroll={{ x: 1000 }}
          rowKey="name"
          loading={isLoading}
          locale={{
            emptyText: <TableErrorEmpty isError={isError} onRetry={() => void refetch()} />,
          }}
          dataSource={nodes}
          pagination={false}
          onRow={(r) => ({ onClick: () => setSelected(r.name), style: { cursor: "pointer" } })}
          columns={[
            {
              title: t("nodes.colNode"),
              dataIndex: "name",
              sorter: (a, b) => a.name.localeCompare(b.name),
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
              // 相对时间直读,hover 给绝对时间;空 = 尚无台账行
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
              render: (_, r) => {
                const cordoned = r.status === "Cordoned";
                return (
                  <Space>
                    <ReasonAction
                      label={cordoned ? t("nodes.uncordonBtn") : t("nodes.cordonBtn")}
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
                    <Tooltip title={t("nodes.drainDeferred")}>
                      <Typography.Text type="secondary">{t("nodes.drainBtn")}</Typography.Text>
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
    </>
  );
}
