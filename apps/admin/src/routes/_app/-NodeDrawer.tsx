/** 节点抽屉(?node= 入 URL 可直链):EntityHeader(名称 / 状态 / 池 / GPU / 驱动 / CUDA / 心跳 + 操作)+ 时间范围 + 每卡热力格(-GpuGrid)+ 节点级曲线(-NodeMetricsPanel)。
 *  NodeActions / PoolTag / GpuModelCell / LastSeenCell 与节点表列共用一份实现。 */

import { Link } from "@tanstack/react-router";
import { Card, Drawer, Segmented, Space, Tag, Tooltip } from "antd";
import dayjs from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { drawerWidth, fontSize, fontWeight, formatDateTime, nodeStatusMap } from "@superdl/ui";
import { EntityHeader, GatedButton, RowActions, StatusTag, type KeyValueItem } from "@superdl/ui/components";

import { type NodeRow, useNodeMetrics } from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { GpuGrid } from "./-GpuGrid";
import { NodeMetricsPanel } from "./-NodeMetricsPanel";

/** 封锁 / 解封提交(经 outbox);反馈与失效由页面处理 */
export type CordonFn = (nodeName: string, on: boolean, reason: string) => Promise<void>;

export function isUnlabeled(n: NodeRow): boolean {
  return n.unlabeled || !n.pool_label;
}

export function PoolTag({ node }: { node: NodeRow }) {
  const { t } = useTranslation();
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
    <Space size={4}>
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

/** 相对时间,hover 给绝对时间;空 = 尚无台账行。 */
export function LastSeenCell({ value }: { value: string | null | undefined }) {
  if (!value) return "—";
  return (
    <Tooltip title={formatDateTime(value)}>
      <span>{dayjs(value).fromNow()}</span>
    </Tooltip>
  );
}

/** 封锁 / 解封(ReasonAction;解封是恢复方向,只填原因不做二次确认)+ drain 占位(可见但禁用 + tooltip);表格行 small、抽屉头 middle。 */
export function NodeActions({
  node,
  writable,
  onCordon,
  size = "small",
}: {
  node: NodeRow;
  writable: boolean;
  onCordon: CordonFn;
  size?: "small" | "middle";
}) {
  const { t } = useTranslation();
  const cordoned = node.status === "Cordoned";
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
          confirm={!cordoned}
          disabled={!writable}
          disabledReason={t("nodes.readonlyNoOp")}
          onSubmit={(reason) => onCordon(node.name, !cordoned, reason)}
        />
      }
      secondary={
        <GatedButton size={size} reason={t("nodes.drainDeferred")}>
          {t("nodes.drainBtn")}
        </GatedButton>
      }
    />
  );
}

function NodeHeader({ node, writable, onCordon }: { node: NodeRow; writable: boolean; onCordon: CordonFn }) {
  const { t } = useTranslation();
  const meta: KeyValueItem[] = [
    { label: t("nodes.colGpu"), value: <GpuModelCell node={node} /> },
    { label: t("nodes.colVram"), value: node.vram_gb ? `${node.vram_gb} G` : null },
    {
      label: t("nodes.colUsed"),
      // 已用卡数链到「租户与实例 › 实例」按节点过滤
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
    // Drawer 标题区默认加粗,元信息条回落正文字重
    <div style={{ fontWeight: fontWeight.regular, fontSize: fontSize.body }}>
      <EntityHeader
        size="drawer"
        name={node.name}
        status={<StatusTag map={nodeStatusMap} value={node.status} variant="badge" icon />}
        subtitle={isUnlabeled(node) ? undefined : node.pool_label}
        // 池标签只在未标注时以红标提示;已标注的池进副标题
        tags={isUnlabeled(node) ? <PoolTag node={node} /> : undefined}
        meta={meta}
        actions={<NodeActions node={node} writable={writable} onCordon={onCordon} size="middle" />}
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
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
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
}: {
  /** undefined = 关闭 */
  node: NodeRow | undefined;
  onClose: () => void;
  writable: boolean;
  onCordon: CordonFn;
}) {
  return (
    <Drawer
      open={node !== undefined}
      onClose={onClose}
      placement="right"
      size={drawerWidth.lg}
      mask={{ closable: true }}
      destroyOnHidden
      title={node && <NodeHeader node={node} writable={writable} onCordon={onCordon} />}
    >
      {node && <NodeDrawerBody node={node} />}
    </Drawer>
  );
}
