import { adminColors, metaOf, nodeEnrollStatusMap, type NodeEnrollStatus } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  App,
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
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type EnrollmentCommandOut,
  type EnrollmentRow,
  type NodeRow,
  isApiError,
  useCordonNode,
  useCreateEnrollment,
  useEnrollments,
  useNodes,
  useRegenerateEnrollment,
  useRevokeEnrollment,
} from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/nodes")({
  component: NodesPage,
});

const PHASE_LABEL: Record<string, string> = {
  bootstrap: "获取参数",
  precheck: "环境检查",
  nouveau: "禁用 nouveau",
  sysctl: "内核参数",
  iommu: "IOMMU",
  driver: "安装驱动",
  nvme_vg: "NVMe VG",
  reboot: "重启生效",
  registries: "镜像 mirror",
  rke2_config: "写入配置",
  rke2_install: "安装 RKE2",
  rke2_start: "启动 agent",
  waiting_node: "等待对账",
  joined: "已加入",
};

function GpuGrid({ node }: { node: NodeRow }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      {Array.from({ length: node.gpu_total }, (_, i) => {
        const used = i < node.gpu_used;
        return (
          <Tooltip
            key={i}
            title={`GPU ${i} · ${used ? "已租" : "空闲"}(util/显存/温度经 Grafana 查看)`}
          >
            <div
              style={{
                width: 44,
                height: 44,
                borderRadius: 6,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 12,
                background: used ? adminColors.dataAccent : adminColors.gridLine,
                color: used ? adminColors.bgBase : adminColors.textMuted,
                fontWeight: 600,
              }}
            >
              {i}
            </div>
          </Tooltip>
        );
      })}
    </div>
  );
}

/** 命令展示(创建/重新生成共用):令牌只显示这一次 */
function CommandPanel({ result }: { result: EnrollmentCommandOut }) {
  return (
    <Space orientation="vertical" size={12} style={{ width: "100%" }}>
      <Alert
        type="warning"
        showIcon
        message="注册令牌只显示这一次"
        description={`有效期至 ${dayjs(result.enrollment.expires_at).format("MM-DD HH:mm")};关闭后无法找回,可随时「重新生成」。在新服务器上以 root 执行:`}
      />
      <div>
        <Typography.Text type="secondary">推荐(管道式):</Typography.Text>
        <Typography.Paragraph copyable code style={{ marginBottom: 8 }}>
          {result.curl_command}
        </Typography.Paragraph>
        <Typography.Text type="secondary">谨慎式(先下载可审阅):</Typography.Text>
        <Typography.Paragraph copyable code style={{ marginBottom: 0 }}>
          {result.wget_command}
        </Typography.Paragraph>
      </div>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        kata 池含一次自动重启(IOMMU/驱动生效,断点续跑);执行失败可修复后重跑同一条命令(全幂等)。
        进度实时显示在下方「待加入节点」。
      </Typography.Text>
    </Space>
  );
}

interface EnrollFormValues {
  pool: "kata" | "hami" | "mig";
  hostname?: string;
  note?: string;
  nvme_devices?: string[];
  ttl_hours: number;
}

function AddNodeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = App.useApp();
  const [form] = Form.useForm<EnrollFormValues>();
  const [result, setResult] = useState<EnrollmentCommandOut | null>(null);
  const [idemKey] = useState(() => crypto.randomUUID());
  const create = useCreateEnrollment({
    mutation: {
      onSuccess: (r) => setResult(r),
      onError: (e) => message.error(isApiError(e) ? e.message : "生成失败"),
    },
  });

  const close = () => {
    setResult(null);
    form.resetFields();
    onClose();
  };

  return (
    <Modal
      title={result ? "节点注册命令" : "添加节点"}
      open={open}
      onCancel={close}
      footer={
        result ? (
          <Button type="primary" onClick={close}>
            完成
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
            生成注册命令
          </Button>
        )
      }
      width={640}
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
            message="分池铁律:装机时定池,Kata 与 HAMi 永不混布"
            description="kata=整卡直通(需 BIOS 开 VT-d,安装含一次自动重启);hami=共享软切分;mig=硬件切分。前置:超管需先在「平台配置 · 集群接入」录入 RKE2 Server 与 join token。"
          />
          <Form.Item name="pool" label="节点池" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "kata", label: "kata(整卡直通)" },
                { value: "hami", label: "hami(共享软切分)" },
                { value: "mig", label: "mig(硬件切分)" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="hostname"
            label="期望主机名(可选;填写后上报不符将拒绝加入,防令牌串用)"
          >
            <Input placeholder="如 gpu-a3-01" />
          </Form.Item>
          <Form.Item name="note" label="备注(可选)">
            <Input placeholder="如 机柜 A3 · 8×4090" maxLength={128} />
          </Form.Item>
          <Form.Item
            name="nvme_devices"
            label="NVMe 设备(可选;填写后装机时创建 TopoLVM VG superdl-nvme)"
            extra="无专用盘的测试节点可显式填 loop:80G,装机时用 loop 文件兜底实例盘(仅验证,非生产性能);留空则该节点无本地实例盘,不会自动兜底。"
          >
            <Select mode="tags" placeholder="如 /dev/nvme0n1;或 loop:80G(回车分隔)" open={false} />
          </Form.Item>
          <Form.Item name="ttl_hours" label="令牌有效期(小时)" rules={[{ required: true }]}>
            <InputNumber min={1} max={168} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      )}
    </Modal>
  );
}

function EnrollmentsCard({ writable }: { writable: boolean }) {
  const { t } = useTranslation(["admin", "shared"]);
  const { message } = App.useApp();
  const qc = useQueryClient();
  const { data, queryKey } = useEnrollments({ active: true, refetchInterval: 5_000 });
  const rows: EnrollmentRow[] = data ?? [];
  const [regenResult, setRegenResult] = useState<EnrollmentCommandOut | null>(null);
  const regenerate = useRegenerateEnrollment({
    mutation: {
      onSuccess: (r) => {
        setRegenResult(r);
        void qc.invalidateQueries({ queryKey });
      },
      onError: (e) => message.error(isApiError(e) ? e.message : "重新生成失败"),
    },
  });
  const revoke = useRevokeEnrollment({
    mutation: { onSuccess: () => void qc.invalidateQueries({ queryKey }) },
  });

  if (rows.length === 0) return null;
  return (
    <Card title="待加入节点" style={{ marginBottom: 16 }}>
      <Table<EnrollmentRow>
        size="small"
        rowKey="id"
        scroll={{ x: 900 }}
        dataSource={rows}
        pagination={false}
        columns={[
          {
            title: "池",
            dataIndex: "pool",
            render: (v: string) => <Tag color="cyan">{v}</Tag>,
          },
          {
            title: "主机名",
            render: (_, r) => r.node_name ?? r.hostname ?? "-",
          },
          { title: "备注", dataIndex: "note", render: (v: string | null) => v ?? "-" },
          {
            title: "状态",
            dataIndex: "status",
            render: (v: NodeEnrollStatus) => {
              const meta = metaOf(nodeEnrollStatusMap, v);
              return meta ? <Badge status={meta.badge} text={t(meta.labelKey)} /> : v;
            },
          },
          {
            title: "阶段",
            dataIndex: "phase",
            render: (v: string | null) => (v ? (PHASE_LABEL[v] ?? v) : "-"),
          },
          {
            title: "最后心跳",
            dataIndex: "last_report_at",
            render: (v: string | null) => (v ? dayjs(v).format("MM-DD HH:mm:ss") : "-"),
          },
          {
            title: "失败原因",
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
            title: "操作",
            width: 190,
            render: (_, r) => (
              <Space>
                <Tooltip
                  title={
                    !writable
                      ? "只读角色不可操作"
                      : ["pending", "expired", "failed"].includes(r.status)
                        ? "换新令牌并展示命令"
                        : "仅 待执行/已过期/已失败 可重新生成"
                  }
                >
                  <Button
                    size="small"
                    disabled={!writable || !["pending", "expired", "failed"].includes(r.status)}
                    loading={regenerate.isPending && regenerate.variables?.enrollmentId === r.id}
                    onClick={() => regenerate.mutate({ enrollmentId: r.id, data: {} })}
                  >
                    重新生成
                  </Button>
                </Tooltip>
                {!["joined", "failed", "expired", "revoked"].includes(r.status) && (
                  <ReasonAction
                    label="吊销"
                    title="吊销注册令牌"
                    confirmText="吊销后该令牌立即失效,进行中的安装将无法继续上报。"
                    danger
                    disabled={!writable}
                    disabledReason="只读角色不可吊销"
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
        title="新注册命令"
        open={regenResult !== null}
        onCancel={() => setRegenResult(null)}
        footer={
          <Button type="primary" onClick={() => setRegenResult(null)}>
            完成
          </Button>
        }
        width={640}
      >
        {regenResult && <CommandPanel result={regenResult} />}
      </Modal>
    </Card>
  );
}

function NodesPage() {
  const { message } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data, refetch } = useNodes();
  const nodes: NodeRow[] = data ?? [];
  const [selected, setSelected] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const node = nodes.find((n) => n.name === selected) ?? nodes[0];
  const cordon = useCordonNode({
    mutation: {
      onSuccess: (_r, v) => {
        message.success(
          `${v.on ? "cordon" : "uncordon"} 已入队,数秒内生效(列表自动刷新)`,
        );
        void qc.invalidateQueries({ queryKey: ["admin", "nodes"] });
        setTimeout(() => void refetch(), 3_000);
      },
      onError: (e) => message.error(isApiError(e) ? e.message : "操作失败"),
    },
  });

  return (
    <>
      <EnrollmentsCard writable={writable} />
      <Card
        title="节点"
        extra={
          <Tooltip title={writable ? "" : "只读角色不可添加"}>
            <Button type="primary" disabled={!writable} onClick={() => setAddOpen(true)}>
              添加节点
            </Button>
          </Tooltip>
        }
      >
        <Table<NodeRow>
          scroll={{ x: 800 }}
          rowKey="name"
          dataSource={nodes}
          pagination={false}
          onRow={(r) => ({ onClick: () => setSelected(r.name), style: { cursor: "pointer" } })}
          columns={[
            { title: "节点", dataIndex: "name" },
            {
              title: "池",
              dataIndex: "pool_label",
              render: (v: string) => <Tag color="cyan">{v}</Tag>,
            },
            {
              title: "GPU",
              render: (_, r) => `${r.gpu_model} × ${r.gpu_total}`,
            },
            { title: "已用", dataIndex: "gpu_used" },
            { title: "驱动", render: (_, r) => r.driver_version || "—" },
            { title: "CUDA", render: (_, r) => r.cuda_version || "—" },
            { title: "CPU", render: (_, r) => `${r.vcpu} 核` },
            { title: "内存", render: (_, r) => `${r.mem_gb} G` },
            { title: "硬盘", render: (_, r) => `${r.disk_gb} G` },
            {
              title: "状态",
              dataIndex: "status",
              render: (v: string) => (
                <Tag color={v === "Ready" ? "green" : v === "Cordoned" ? "orange" : "red"}>{v}</Tag>
              ),
            },
            {
              title: "操作",
              width: 170,
              render: (_, r) => {
                const cordoned = r.status === "Cordoned";
                return (
                  <Space>
                    <ReasonAction
                      label={cordoned ? "uncordon" : "cordon"}
                      title={cordoned ? "恢复调度" : "停止调度"}
                      confirmText={
                        cordoned
                          ? `恢复 ${r.name} 的调度,新实例可再落到该节点。`
                          : `停止 ${r.name} 的调度:存量实例不受影响,新实例不再落到该节点(经 outbox 数秒内生效)。`
                      }
                      danger={!cordoned}
                      disabled={!writable}
                      disabledReason="只读角色不可操作"
                      onSubmit={async (reason) => {
                        await cordon.mutateAsync({
                          nodeName: r.name,
                          on: !cordoned,
                          data: { reason },
                        });
                      }}
                    />
                    <Tooltip title="drain(驱逐)牵扯计费与迁移策略,后置;当前经集群 Runbook 执行">
                      <Typography.Text type="secondary">drain</Typography.Text>
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
        <Card title={`每卡视图 · ${node.name}`} style={{ marginTop: 16 }}>
          <GpuGrid node={node} />
        </Card>
      )}
      <Card title="节点历史曲线(Grafana)" style={{ marginTop: 16 }}>
        <Alert
          type="info"
          showIcon
          message="生产环境此处嵌入 Grafana DCGM 大盘(iframe)"
          description="部署要求:Grafana 13 开启 allow_embedding=true,经反向代理注入只读 Viewer 身份;面板以社区 24450 为底改造。本地开发环境无 Grafana,显示此占位。"
        />
      </Card>
    </>
  );
}
