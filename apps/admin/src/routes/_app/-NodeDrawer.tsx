/** Node drawer: node facts, actions, GPU heat grid and historical metrics. */

import { Link } from "@tanstack/react-router";
import { Card, Drawer, Segmented, Space, Tag, Tooltip } from "antd";
import dayjs from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import { drawerWidth, fontSize, fontWeight, formatDateTime, nodeStatusMap, space } from "@superdl/ui";
import { EntityHeader, GatedButton, RowActions, StatusTag, type KeyValueItem } from "@superdl/ui/components";

import { type NodeRow, useNodeMetrics } from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { GpuGrid } from "./-GpuGrid";
import { canSwitchPool, inSwitchablePool } from "./-SwitchPoolModal";
import { NodeMetricsPanel } from "./-NodeMetricsPanel";

/** Cordon / uncordon submit (through the outbox); feedback and invalidation handled by the page */
export type CordonFn = (nodeName: string, on: boolean, reason: string) => Promise<void>;

export function isUnlabeled(n: NodeRow): boolean {
  return n.unlabeled || !n.pool_label;
}

/** Pool switch in progress: desired pool set and different from the self-declared pool (labels not yet converged). */
export function isSwitching(n: NodeRow): boolean {
  return !!n.desired_pool && n.desired_pool !== n.pool_label;
}

/** During a switch show "old → new"; unlabelled in red, the rest in cyan. */
export function PoolTag({ node }: { node: NodeRow }) {
  const { t } = useTranslation();
  if (isSwitching(node)) {
    return <Tag color="processing">{`${node.pool_label || "—"} → ${node.desired_pool}`}</Tag>;
  }
  return isUnlabeled(node) ? (
    <Tag color="red">{t("nodes.unlabeledTag")}</Tag>
  ) : (
    <Tag color="cyan">{node.pool_label}</Tag>
  );
}

/** GPU model × count; unrecognised models / unsynced model labels each carry a marker. */
export function GpuModelCell({ node }: { node: NodeRow }) {
  const { t } = useTranslation();
  const unrecognized = node.gpu_model === "GPU" && !!node.gpu_model_raw;
  return (
    <Space size={space.xs}>
      <span>{`${unrecognized ? node.gpu_model_raw : node.gpu_model} × ${node.gpu_total}`}</span>
      {unrecognized && <Tag color="gold">{t("nodes.unrecognizedTag")}</Tag>}
      {!unrecognized && node.gpu_model !== "GPU" && node.label_synced === false && (
        <Tooltip title={t("nodes.labelUnsynced")}>
          <Tag color="orange">!</Tag>
        </Tooltip>
      )}
    </Space>
  );
}

/** Unreleased instance count (stopped included); non-zero links to the node's instance list. */
export function InstancesCell({ node }: { node: NodeRow }) {
  const n = activeInstances(node);
  if (n === 0) return 0;
  return (
    <Link to="/tenants" search={{ tab: "instances", inode: node.name }}>
      {n}
    </Link>
  );
}

/** Relative time, absolute time on hover; empty = no ledger row yet. */
export function LastSeenCell({ value }: { value: string | null | undefined }) {
  if (!value) return "—";
  return (
    <Tooltip title={formatDateTime(value)}>
      <span>{dayjs(value).fromNow()}</span>
    </Tooltip>
  );
}

/** Unreleased instances on the node (stopped included): the shared precondition of pool switch and decommission. */
export function activeInstances(n: NodeRow): number {
  return n.active_instances ?? 0;
}

/** Greyed reason when the pool-switch / decommission precondition fails; undefined = available. */
function blockedReason(node: NodeRow, writable: boolean, t: TFunction): string | undefined {
  if (!writable) return t("nodes.readonlyNoOp");
  if (activeInstances(node) > 0) return t("nodes.nodeBusy", { count: activeInstances(node) });
  return undefined;
}

/** Cordon / uncordon (ReasonAction; uncordon is the recovery direction, reason only without a second confirmation) + switch pool + more (evict placeholder / decommission);
 *  small in table rows, middle in the drawer head. */
export function NodeActions({
  node,
  writable,
  onCordon,
  onSwitchPool,
  onDecommission,
  size = "small",
}: {
  node: NodeRow;
  writable: boolean;
  onCordon: CordonFn;
  onSwitchPool: (node: NodeRow) => void;
  onDecommission: (node: NodeRow) => void;
  size?: "small" | "middle";
}) {
  const { t } = useTranslation();
  const cordoned = node.status === "Cordoned";
  const blocked = blockedReason(node, writable, t);
  const switchable = !isUnlabeled(node) && canSwitchPool(node);
  const switchBlocked = inSwitchablePool(node) ? t("nodes.switchPoolNoTarget") : t("nodes.switchPoolUnavailable");
  return (
    <RowActions
      size={size}
      primary={
        <ReasonAction
          size={size}
          label={cordoned ? t("nodes.uncordonBtn") : t("nodes.cordonBtn")}
          target={node.name}
          title={cordoned ? t("nodes.uncordonTitle") : t("nodes.cordonTitle")}
          confirmText={
            cordoned ? t("nodes.uncordonConfirm", { name: node.name }) : t("nodes.cordonConfirm", { name: node.name })
          }
          danger={!cordoned}
          triggerDanger={false}
          confirm={!cordoned}
          disabled={!writable}
          disabledReason={t("nodes.readonlyNoOp")}
          onSubmit={(reason) => onCordon(node.name, !cordoned, reason)}
        />
      }
      secondary={
        <GatedButton size={size} reason={switchable ? blocked : switchBlocked} onClick={() => onSwitchPool(node)}>
          {t("nodes.switchPoolBtn")}
        </GatedButton>
      }
      more={[
        { key: "drain", label: t("nodes.drainBtn"), reason: t("nodes.drainDeferred"), onClick: () => undefined },
        {
          key: "decommission",
          label: t("nodes.decommissionBtn"),
          danger: true,
          reason: writable ? undefined : t("nodes.readonlyNoOp"),
          onClick: () => onDecommission(node),
        },
      ]}
    />
  );
}

function NodeHeader({
  node,
  writable,
  onCordon,
  onSwitchPool,
  onDecommission,
}: {
  node: NodeRow;
  writable: boolean;
  onCordon: CordonFn;
  onSwitchPool: (node: NodeRow) => void;
  onDecommission: (node: NodeRow) => void;
}) {
  const { t } = useTranslation();
  const meta: KeyValueItem[] = [
    { label: t("nodes.colGpu"), value: <GpuModelCell node={node} /> },
    { label: t("nodes.colVram"), value: node.vram_gb ? `${node.vram_gb} G` : null },
    { label: t("nodes.colInstances"), value: <InstancesCell node={node} /> },
    {
      label: t("nodes.colUsed"),
      value:
        node.gpu_used > 0 ? (
          <Link to="/tenants" search={{ tab: "instances", inode: node.name }}>
            {node.gpu_used}
          </Link>
        ) : (
          node.gpu_used
        ),
    },
    { label: t("nodes.colDriver"), value: node.driver_version || null, mono: true },
    { label: "CUDA", value: node.cuda_version || null, mono: true },
    { label: t("nodes.colCpu"), value: t("nodes.coreCount", { count: node.vcpu }) },
    { label: t("nodes.colMem"), value: `${node.mem_gb} G` },
    { label: t("nodes.colDisk"), value: `${node.disk_gb} G` },
    { label: t("nodes.colLastSeen"), value: node.last_seen ? <LastSeenCell value={node.last_seen} /> : null },
  ];
  return (
    <div style={{ fontWeight: fontWeight.regular, fontSize: fontSize.body }}>
      <EntityHeader
        size="drawer"
        name={node.name}
        status={<StatusTag map={nodeStatusMap} value={node.status} variant="badge" icon />}
        subtitle={isUnlabeled(node) || isSwitching(node) ? undefined : node.pool_label}
        tags={isUnlabeled(node) || isSwitching(node) ? <PoolTag node={node} /> : undefined}
        meta={meta}
        actions={
          <NodeActions
            node={node}
            writable={writable}
            onCordon={onCordon}
            onSwitchPool={onSwitchPool}
            onDecommission={onDecommission}
            size="middle"
          />
        }
      />
    </div>
  );
}

/** Drawer body: the time range feeds both the heat grid (last sample) and the curves; reset when the drawer is destroyed. */
function NodeDrawerBody({ node }: { node: NodeRow }) {
  const { t } = useTranslation();
  const [range, setRange] = useState("1h");
  const { data: metrics } = useNodeMetrics(node.name, range);
  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
      <Segmented
        value={range}
        onChange={setRange}
        options={[
          { value: "1h", label: t("nodes.range1h") },
          { value: "6h", label: t("nodes.range6h") },
          { value: "24h", label: t("nodes.range24h") },
        ]}
      />
      <Card title={t("nodes.gpuGridTitle", { name: node.name })}>
        <GpuGrid node={node} metrics={metrics} />
      </Card>
      <NodeMetricsPanel node={node} metrics={metrics} />
    </Space>
  );
}

export function NodeDrawer({
  node,
  onClose,
  writable,
  onCordon,
  onSwitchPool,
  onDecommission,
}: {
  /** undefined = closed */
  node: NodeRow | undefined;
  onClose: () => void;
  writable: boolean;
  onCordon: CordonFn;
  onSwitchPool: (node: NodeRow) => void;
  onDecommission: (node: NodeRow) => void;
}) {
  return (
    <Drawer
      open={node !== undefined}
      onClose={onClose}
      placement="right"
      size={drawerWidth.lg}
      mask={{ closable: true }}
      destroyOnHidden
      title={
        node && (
          <NodeHeader
            node={node}
            writable={writable}
            onCordon={onCordon}
            onSwitchPool={onSwitchPool}
            onDecommission={onDecommission}
          />
        )
      }
    >
      {node && <NodeDrawerBody node={node} />}
    </Drawer>
  );
}
