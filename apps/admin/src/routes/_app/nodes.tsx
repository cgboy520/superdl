/** Nodes page: enrollments, ledger filters, node actions and the ?node= detail drawer. */

import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Alert, App, Button, Card, Input, Select, Space, Table, Tag, Tooltip, theme } from "antd";
import dayjs from "dayjs";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  controlWidth,
  layout,
  nodeStatusMap,
  POLL,
  space,
  useAutoRefresh,
  useUrlCommittedInput,
  useUrlFilters,
} from "@superdl/ui";
import {
  EmptyState,
  FilterBar,
  GatedButton,
  Mono,
  PageContainer,
  TableErrorEmpty,
  StatusTag,
  TypeConfirmModal,
} from "@superdl/ui/components";
import { useApiErrorText } from "@superdl/ui";

import {
  adminKeys,
  type NodeRow,
  isApiError,
  useCordonNode,
  useDecommissionNode,
  useNodes,
  usePortPool,
} from "../../api";
import { BulkBar, runBulk } from "../../components/BulkBar";
import { ReasonAction } from "../../components/ReasonAction";
import { canWriteOps, useAdminRole } from "../../stores/auth";
import { AddNodeModal } from "./-AddNodeModal";
import { EnrollmentsCard } from "./-EnrollmentsCard";
import {
  activeInstances,
  type CordonFn,
  GpuModelCell,
  InstancesCell,
  isUnlabeled,
  LastSeenCell,
  NodeActions,
  NodeDrawer,
  PoolTag,
} from "./-NodeDrawer";
import { SwitchPoolModal } from "./-SwitchPoolModal";

const NODE_STATUSES = ["Ready", "NotReady", "Cordoned", "Missing"] as const;
type NodeStatus = (typeof NODE_STATUSES)[number];
/** URL value of "unlabelled" in the pool filter (does not collide with real pool labels) */
const POOL_UNLABELED = "unlabeled";

export const Route = createFileRoute("/_app/nodes")({
  validateSearch: (
    search: Record<string, unknown>,
  ): { node?: string; q?: string; pool?: string; status?: NodeStatus } => ({
    node: typeof search.node === "string" && search.node ? search.node : undefined,
    q: typeof search.q === "string" && search.q.trim() ? search.q : undefined,
    pool: typeof search.pool === "string" && search.pool ? search.pool : undefined,
    status: NODE_STATUSES.includes(search.status as NodeStatus) ? (search.status as NodeStatus) : undefined,
  }),
  component: NodesPage,
});

/** Inline links / buttons / checkboxes handle their own clicks and no longer bubble into "open drawer" */
function fromInteractive(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest("a, button, input, label") !== null;
}

function NodesPage() {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const { token } = theme.useToken();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const navigate = useNavigate({ from: "/nodes" });
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
  const { node: nodeParam, q: urlQ, pool, status: statusFilter } = Route.useSearch();
  const [addOpen, setAddOpen] = useState(false);
  const setUrl = useCallback(
    (patch: { q?: string; pool?: string; status?: NodeStatus; node?: string }) =>
      void navigate({ to: "/nodes", replace: true, search: (prev) => ({ ...prev, ...patch }) }),
    [navigate],
  );
  const commitQ = useCallback((next: string | undefined) => setUrl({ q: next }), [setUrl]);
  const { value: kwInput, setValue: setKwInput } = useUrlCommittedInput(urlQ, commitQ);
  const filters = useUrlFilters({
    search: { q: urlQ, pool, status: statusFilter },
    keys: ["q", "pool", "status"],
    commit: setUrl,
  });
  const kw = urlQ?.trim().toLowerCase();
  const filteredNodes = useMemo(
    () =>
      nodes.filter((n) => {
        if (kw && !n.name.toLowerCase().includes(kw)) return false;
        if (pool && (pool === POOL_UNLABELED ? !isUnlabeled(n) : isUnlabeled(n) || n.pool_label !== pool)) return false;
        if (statusFilter && n.status !== statusFilter) return false;
        return true;
      }),
    [nodes, kw, pool, statusFilter],
  );
  const poolOptions = useMemo(() => {
    const opts = [...new Set(nodes.filter((n) => !isUnlabeled(n)).map((n) => n.pool_label))].map((p) => ({
      value: p,
      label: p,
    }));
    if (nodes.some(isUnlabeled)) opts.push({ value: POOL_UNLABELED, label: t("nodes.unlabeledTag") });
    return opts;
  }, [nodes, t]);
  const node = nodeParam === undefined ? undefined : nodes.find((n) => n.name === nodeParam);
  const deepLinkMissing = nodeParam !== undefined && data !== undefined && node === undefined;
  const openNode = useCallback((name: string) => setUrl({ node: name }), [setUrl]);
  const closeNode = useCallback(() => setUrl({ node: undefined }), [setUrl]);
  useEffect(() => {
    if (!nodeParam || nodes.length === 0) return;
    const row = document.querySelector(`[data-row-key="${CSS.escape(nodeParam)}"]`);
    row?.scrollIntoView({ block: "center" });
  }, [nodeParam, nodes.length]);
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
  const cordonNode: CordonFn = async (nodeName, on, reason) => {
    await cordon.mutateAsync({ nodeName, on, data: { reason } });
  };
  const [switching, setSwitching] = useState<NodeRow | undefined>();
  const refreshNodes = useCallback(() => {
    void qc.invalidateQueries({ queryKey: adminKeys.nodes });
    void qc.invalidateQueries({ queryKey: adminKeys.enrollments });
    cordonTimer.current = setTimeout(() => void qc.invalidateQueries({ queryKey: adminKeys.nodes }), 3_000);
  }, [qc]);
  const [retiring, setRetiring] = useState<NodeRow | undefined>();
  const [retireReason, setRetireReason] = useState("");
  const [retireForce, setRetireForce] = useState(false);
  const decommission = useDecommissionNode({
    mutation: {
      onSuccess: (r) => {
        message.success(t("nodes.decommissionSubmitted", { count: r.revoked_enrollments }));
        setRetiring(undefined);
        setRetireReason("");
        setRetireForce(false);
        refreshNodes();
      },
      onError: (e) => {
        if (isApiError(e) && e.status === 409) setRetireForce(true);
        message.error(errText(e, t("common.actionFailed", { action: t("nodes.decommissionTitle") })));
      },
    },
  });
  const openRetire = useCallback((n: NodeRow) => {
    setRetiring(n);
    setRetireReason("");
    setRetireForce(false);
  }, []);
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
        <Space size={space.md}>
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
        <FilterBar hasFilter={filters.hasFilter} onClear={filters.clear}>
          <Input.Search
            allowClear
            placeholder={t("nodes.searchPlaceholder")}
            style={{ width: controlWidth.md }}
            value={kwInput}
            onChange={(e) => setKwInput(e.target.value)}
            onSearch={(v) => commitQ(v.trim() || undefined)}
          />
          <Select
            allowClear
            placeholder={t("nodes.filterPool")}
            style={{ width: controlWidth.sm }}
            value={pool}
            onChange={(v: string | undefined) => setUrl({ pool: v })}
            options={poolOptions}
          />
          <Select
            allowClear
            placeholder={t("common.statusFilter")}
            style={{ width: controlWidth.sm }}
            value={statusFilter}
            onChange={(v: NodeStatus | undefined) => setUrl({ status: v })}
            options={NODE_STATUSES.map((s) => ({ value: s, label: t(nodeStatusMap[s].labelKey) }))}
          />
        </FilterBar>
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
            confirm={false}
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
            emptyText: isError ? (
              <TableErrorEmpty
                isError
                isForbidden={isApiError(error) && error.status === 403}
                onRetry={() => void refetch()}
              />
            ) : filters.hasFilter ? (
              <EmptyState
                scene="search"
                compact
                description={t("nodes.noMatch")}
                secondaryAction={
                  <Button size="small" onClick={filters.clear}>
                    {t("filter.clear", { ns: "shared" })}
                  </Button>
                }
              />
            ) : (
              <EmptyState scene="list" compact />
            ),
          }}
          dataSource={filteredNodes}
          pagination={
            filteredNodes.length > 200 ? { pageSize: 100, showSizeChanger: false, hideOnSinglePage: true } : false
          }
          onRow={(r) => ({
            onClick: (e) => {
              if (!fromInteractive(e.target)) openNode(r.name);
            },
            tabIndex: 0,
            onKeyDown: (e) => {
              if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) {
                e.preventDefault();
                openNode(r.name);
              }
            },
            style: {
              cursor: "pointer",
              ...(r.name === nodeParam
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
              render: (v: string) => <Mono>{v}</Mono>,
            },
            {
              title: t("nodes.colStatus"),
              dataIndex: "status",
              width: 110,
              render: (v: string) => <StatusTag map={nodeStatusMap} value={v} variant="badge" icon />,
            },
            {
              title: t("nodes.colPool"),
              dataIndex: "pool_label",
              render: (_, r) => <PoolTag node={r} />,
            },
            {
              title: t("nodes.colGpu"),
              render: (_, r) => <GpuModelCell node={r} />,
            },
            {
              title: t("nodes.colVram"),
              align: "right",
              render: (_, r) => (r.vram_gb ? `${r.vram_gb} G` : "—"),
            },
            {
              title: t("nodes.colUsed"),
              dataIndex: "gpu_used",
              align: "right",
              sorter: (a, b) => a.gpu_used - b.gpu_used,
              render: (v: number, r) =>
                v > 0 ? (
                  <Link to="/tenants" search={{ tab: "instances", inode: r.name }}>
                    {v}
                  </Link>
                ) : (
                  v
                ),
            },
            {
              title: t("nodes.colInstances"),
              dataIndex: "active_instances",
              align: "right",
              sorter: (a, b) => activeInstances(a) - activeInstances(b),
              render: (_, r) => <InstancesCell node={r} />,
            },
            { title: t("nodes.colDriver"), render: (_, r) => r.driver_version || "—" },
            { title: "CUDA", render: (_, r) => r.cuda_version || "—" },
            { title: t("nodes.colCpu"), align: "right", render: (_, r) => t("nodes.coreCount", { count: r.vcpu }) },
            {
              title: t("nodes.colMem"),
              align: "right",
              render: (_, r) => `${r.mem_gb} G`,
              sorter: (a, b) => a.mem_gb - b.mem_gb,
            },
            {
              title: t("nodes.colDisk"),
              align: "right",
              render: (_, r) => `${r.disk_gb} G`,
              sorter: (a, b) => a.disk_gb - b.disk_gb,
            },
            {
              title: t("nodes.colLastSeen"),
              dataIndex: "last_seen",
              width: 130,
              sorter: (a, b) => dayjs(a.last_seen || 0).valueOf() - dayjs(b.last_seen || 0).valueOf(),
              render: (v: string) => <LastSeenCell value={v} />,
            },
            {
              title: t("nodes.colActions"),
              width: 230,
              fixed: "right",
              render: (_, r) => (
                <NodeActions
                  node={r}
                  writable={writable}
                  onCordon={cordonNode}
                  onSwitchPool={setSwitching}
                  onDecommission={openRetire}
                />
              ),
            },
          ]}
        />
      </Card>
      <AddNodeModal open={addOpen} onClose={() => setAddOpen(false)} />
      <SwitchPoolModal node={switching} onClose={() => setSwitching(undefined)} onDone={refreshNodes} />
      {retiring && (
        <TypeConfirmModal
          open
          title={t("nodes.decommissionTitle")}
          targetName={retiring.name}
          body={
            <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
              <span>{t("nodes.decommissionBody", { name: retiring.name })}</span>
              <span>{t("nodes.decommissionBoundary")}</span>
              {activeInstances(retiring) > 0 && (
                <Alert
                  type="error"
                  showIcon
                  title={t("nodes.nodeBusy", { count: activeInstances(retiring) })}
                  description={t("nodes.decommissionForceHint")}
                />
              )}
              <Input.TextArea
                rows={2}
                maxLength={200}
                showCount
                value={retireReason}
                onChange={(e) => setRetireReason(e.target.value)}
                placeholder={t("common.reasonPlaceholder")}
                aria-label={t("common.reasonLabel")}
              />
            </Space>
          }
          checkboxLabel={t("nodes.decommissionAck")}
          confirmLabel={activeInstances(retiring) > 0 ? t("nodes.decommissionForce") : t("nodes.decommissionBtn")}
          cancelLabel={t("common.cancel", { ns: "shared" })}
          loading={decommission.isPending}
          extraDisabled={retireReason.trim().length < 2}
          onConfirm={() =>
            decommission.mutate({
              nodeName: retiring.name,
              data: { reason: retireReason.trim() },
              force: retireForce || activeInstances(retiring) > 0,
            })
          }
          onCancel={() => {
            setRetiring(undefined);
            setRetireReason("");
            setRetireForce(false);
          }}
        />
      )}
      <NodeDrawer
        node={node}
        onClose={closeNode}
        writable={writable}
        onCordon={cordonNode}
        onSwitchPool={setSwitching}
        onDecommission={openRetire}
      />
    </PageContainer>
  );
}
