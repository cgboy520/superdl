/** 待加入节点卡:注册记录表(阶段 / 心跳 / 失败原因)+ 重新生成 / 吊销。 */

import { useQueryClient } from "@tanstack/react-query";
import { Radio, Badge, Button, Card, Modal, Space, Table, Tag, Tooltip, Typography } from "antd";
import dayjs from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { formatDateTime, metaOf, nodeEnrollStatusMap, type NodeEnrollStatus } from "@superdl/ui";
import { TableErrorEmpty } from "@superdl/ui/components";

import {
  type EnrollmentCommandOut,
  type EnrollmentRow,
  isApiError,
  useEnrollments,
  useRegenerateEnrollment,
  useRevokeEnrollment,
} from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { CommandPanel } from "./-AddNodeModal";

export const PHASE_LABEL = {
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

export function EnrollmentsCard({ writable }: { writable: boolean }) {
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
    >
      {" "}
      <Table<EnrollmentRow>
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
                  disabledReason={!writable ? t("nodes.readonlyNoOp") : t("nodes.regenerateOnly")}
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
