/** 节点与 GPU:待加入节点卡(-EnrollmentsCard)+ 节点台账(热力格 -GpuGrid、选中节点指标 -NodeMetricsPanel、添加节点 -AddNodeModal);?node= 深链定位行。 */

import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Alert, App, Card, Input, Space, Table, Tag, Tooltip, theme } from "antd";
import dayjs from "dayjs";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { formatDateTime, layout, POLL, space, useAutoRefresh } from "@superdl/ui";
import { GatedButton, PageContainer, TableErrorEmpty } from "@superdl/ui/components";
import { useApiErrorText } from "@superdl/ui";

import { adminKeys, type NodeRow, isApiError, useNodeMetrics, useCordonNode, useNodes, usePortPool } from "../../api";
import { BulkBar, runBulk } from "../../components/BulkBar";
import { ReasonAction } from "../../components/ReasonAction";
import { canWriteOps, useAdminRole } from "../../stores/auth";
import { GpuGrid } from "./-GpuGrid";
import { NodeMetricsPanel } from "./-NodeMetricsPanel";
import { AddNodeModal } from "./-AddNodeModal";
import { EnrollmentsCard } from "./-EnrollmentsCard";

export const Route = createFileRoute("/_app/nodes")({
  // node:告警深链(/nodes?node=<name>)目标行
  validateSearch: (search: Record<string, unknown>): { node?: string } => ({
    node: typeof search.node === "string" && search.node ? search.node : undefined,
  }),
  component: NodesPage,
});

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
  const deepLinkMissing = nodeParam !== undefined && data !== undefined && !nodes.some((n) => n.name === nodeParam);
  const node =
    deepLinkMissing && selected === nodeParam ? undefined : (nodes.find((n) => n.name === selected) ?? nodes[0]);
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
        void qc.invalidateQueries({ queryKey: adminKeys.nodes });
        if (r.queued) {
          cordonTimer.current = setTimeout(() => void qc.invalidateQueries({ queryKey: adminKeys.nodes }), 3_000);
        }
      },
      onError: (e, v) =>
        message.error(
          errText(
            e,
            t("common.actionFailed", {
              action: v.on ? t("nodes.actionCordon") : t("nodes.actionUncordon"),
            }),
          ),
        ),
    },
  });
  // 批量 cordon / uncordon:一条原因作用于全部所选,逐条并发
  const [bulkSelected, setBulkSelected] = useState<string[]>([]);
  const bulkCordon = async (on: boolean, reason: string) => {
    const { ok, failed } = await runBulk(bulkSelected, (name) =>
      cordon.mutateAsync({ nodeName: name, on, data: { reason } }),
    );
    setBulkSelected([]);
    void qc.invalidateQueries({ queryKey: adminKeys.nodes });
    if (failed > 0) message.warning(t("bulk.partial", { ok, failed }));
    return t("bulk.done", { count: ok });
  };
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
              <Tag color={portPool.blocked > 0 ? "red" : "default"} style={{ marginInlineEnd: 0 }}>
                {t("nodes.portPool", {
                  assigned: portPool.assigned,
                  total: portPool.total,
                  blocked: portPool.blocked,
                })}
              </Tag>
            </Tooltip>
          )}
          <GatedButton
            type="primary"
            reason={writable ? undefined : t("nodes.readonlyNoAdd")}
            onClick={() => setAddOpen(true)}
          >
            {t("nodes.addNode")}
          </GatedButton>
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
        <BulkBar count={bulkSelected.length} onClear={() => setBulkSelected([])}>
          <ReasonAction
            label={t("nodes.cordonBtn")}
            target={t("bulk.selected", { count: bulkSelected.length })}
            title={t("nodes.cordonTitle")}
            confirmText={t("bulk.cordonConfirm", { count: bulkSelected.length })}
            disabled={!writable}
            disabledReason={t("nodes.readonlyNoOp")}
            onSubmit={(reason) => bulkCordon(true, reason)}
          />
          <ReasonAction
            label={t("nodes.uncordonBtn")}
            target={t("bulk.selected", { count: bulkSelected.length })}
            title={t("nodes.uncordonTitle")}
            confirmText={t("bulk.uncordonConfirm", { count: bulkSelected.length })}
            disabled={!writable}
            disabledReason={t("nodes.readonlyNoOp")}
            onSubmit={(reason) => bulkCordon(false, reason)}
          />
        </BulkBar>
        <Table<NodeRow>
          scroll={{ x: 1200 }}
          sticky={{ offsetHeader: layout.topBarHeight }}
          rowKey="name"
          loading={isLoading}
          rowSelection={
            writable
              ? { selectedRowKeys: bulkSelected, onChange: (keys) => setBulkSelected(keys.map(String)), fixed: true }
              : undefined
          }
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
            filteredNodes.length > 200 ? { pageSize: 100, showSizeChanger: false, hideOnSinglePage: true } : false
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
                r.unlabeled || !v ? <Tag color="red">{t("nodes.unlabeledTag")}</Tag> : <Tag color="cyan">{v}</Tag>,
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
                  color={v === "Ready" ? "green" : v === "Cordoned" ? "orange" : v === "Missing" ? "default" : "red"}
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
                    <GatedButton size="small" reason={t("nodes.drainDeferred")}>
                      {t("nodes.drainBtn")}
                    </GatedButton>
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
          <NodeMetricsPanel node={node} metrics={nodeMetrics} range={range} onRangeChange={setRange} />
        </>
      )}
    </PageContainer>
  );
}
