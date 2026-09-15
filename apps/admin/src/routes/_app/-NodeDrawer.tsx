/** 节点抽屉:节点信息、操作、GPU 热力格与历史指标。 */

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

/** 封锁 / 解封提交(经 outbox);反馈与失效由页面处理 */
export type CordonFn = (nodeName: string, on: boolean, reason: string) => Promise<void>;

export function isUnlabeled(n: NodeRow): boolean {
  return n.unlabeled || !n.pool_label;
}

/** 切池进行中:期望池非空且与自声明池不同(标签还没收敛到位)。 */
export function isSwitching(n: NodeRow): boolean {
  return !!n.desired_pool && n.desired_pool !== n.pool_label;
}

/** 切池中显示「旧 → 新」,未打标红标,其余青标。 */
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

/** GPU 型号 × 数量;未识别型号 / 型号标签未同步各带标记。 */
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

/** 未释放实例数(含已关机);非零时链接到该节点的实例列表。 */
export function InstancesCell({ node }: { node: NodeRow }) {
  const n = activeInstances(node);
  if (n === 0) return 0;
  return (
    <Link to="/tenants" search={{ tab: "instances", inode: node.name }}>
      {n}
    </Link>
  );
}

/** 相对时间,hover 给绝对时间;空 = 尚无台账行。 */
export function LastSeenCell({ value }: { value: string | null | undefined }) {
  if (!value) return "—";
  return (
    <Tooltip title={formatDateTime(value)}>
      <span>{dayjs(value).fromNow()}</span>
    </Tooltip>
  );
}

/** 节点上未释放实例数(含已关机):切池与退役的共同前置。 */
export function activeInstances(n: NodeRow): number {
  return n.active_instances ?? 0;
}

/** 切池 / 退役的前置不满足时的灰置原因;undefined = 可用。 */
function blockedReason(node: NodeRow, writable: boolean, t: TFunction): string | undefined {
  if (!writable) return t("nodes.readonlyNoOp");
  if (activeInstances(node) > 0) return t("nodes.nodeBusy", { count: activeInstances(node) });
  return undefined;
}

/** 封锁 / 解封(ReasonAction;解封是恢复方向,只填原因不做二次确认)+ 切换池 + 更多(驱逐占位 / 退役);
 *  表格行 small、抽屉头 middle。 */
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

/** 抽屉体:时间范围同时喂热力格(取末样本)与曲线;随抽屉销毁重置。 */
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
  /** undefined = 关闭 */
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
