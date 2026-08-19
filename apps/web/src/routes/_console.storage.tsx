/** 存储(差异化统一页):挂载全景图 + 数据盘列表(扩容抽屉/多级删除防护)。 */

import { type DiskOut } from "@superdl/api-client";
import { copy, formatDateTime, formatSizeGb, statusColors } from "@superdl/ui";
import { createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Slider,
  Space,
  Table,
  Tooltip,
  Typography,
} from "antd";
import { useState } from "react";

import { useCreateDisk, useDeleteDisk, useExpandDisk } from "../api/mutations";
import { useDisks, useInstances } from "../api/queries";
import { DiskStatusBadge } from "../components/common";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/storage")({
  beforeLoad: requireAuth,
  component: StoragePage,
});

const DISK_PRICE = 0.035;

function MountOverview() {
  const seg = (title: string, desc: string, color: string) => (
    <div
      style={{
        flex: 1,
        borderTop: `3px solid ${color}`,
        padding: "8px 12px",
        background: "#fff",
      }}
    >
      <Typography.Text strong>{title}</Typography.Text>
      <br />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {desc}
      </Typography.Text>
    </div>
  );
  return (
    <Card size="small" title="挂载全景图(每台实例的目录布局)">
      <div style={{ display: "flex", gap: 8 }}>
        {seg("/", "实例盘 · 含 100G · 随实例回收 · 免费", statusColors.blue)}
        {seg("/root/data", `数据盘 · ¥${DISK_PRICE}/GB·月 · 独立保留`, "#4F46E5")}
        {seg("/public/models", "公共模型缓存 · 只读 · 免费", statusColors.green)}
      </div>
    </Card>
  );
}

function DeleteDiskModal({ disk, onClose }: { disk: DiskOut | null; onClose: () => void }) {
  const [checked, setChecked] = useState(false);
  const { message } = App.useApp();
  const del = useDeleteDisk({
    onSuccess: () => {
      message.success("数据盘已开始清除");
      onClose();
    },
  });
  return (
    <Modal
      title="删除数据盘"
      open={Boolean(disk)}
      onCancel={() => {
        setChecked(false);
        onClose();
      }}
      footer={
        <Button
          danger
          type="primary"
          disabled={!checked}
          loading={del.isPending}
          onClick={() => disk && del.mutate(disk.uuid)}
        >
          确认删除
        </Button>
      }
    >
      <Typography.Paragraph>
        即将删除数据盘{" "}
        <Typography.Text strong>
          {disk?.name}({formatSizeGb(disk?.size_gb ?? 0)})
        </Typography.Text>
        ,盘内全部数据将被清除且不可恢复。
      </Typography.Paragraph>
      <Checkbox checked={checked} onChange={(e) => setChecked(e.target.checked)}>
        我确认清除该数据盘的全部数据
      </Checkbox>
    </Modal>
  );
}

function StoragePage() {
  const { message } = App.useApp();
  const { data: disks, isLoading } = useDisks({ refetchInterval: 10_000 });
  const { data: instances } = useInstances();
  const [createOpen, setCreateOpen] = useState(false);
  const [expandTarget, setExpandTarget] = useState<DiskOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DiskOut | null>(null);
  const [newSize, setNewSize] = useState(100);
  const [form] = Form.useForm();

  const createDisk = useCreateDisk({
    onSuccess: () => {
      message.success("数据盘已创建");
      setCreateOpen(false);
      form.resetFields();
    },
  });
  const expand = useExpandDisk({
    onSuccess: () => {
      message.success("扩容完成");
      setExpandTarget(null);
    },
  });

  const instanceName = (id: number | null) =>
    id == null ? "—" : (instances ?? []).find((i) => i.id === id)?.name ?? `#${id}`;

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Space style={{ width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          存储
        </Typography.Title>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          新建数据盘
        </Button>
      </Space>
      <MountOverview />
      <Card>
        {(disks ?? []).length === 0 && !isLoading ? (
          <Empty description={copy.diskRetention}>
            <Button type="primary" onClick={() => setCreateOpen(true)}>
              创建第一块数据盘
            </Button>
          </Empty>
        ) : (
          <Table<DiskOut>
            rowKey="uuid"
            loading={isLoading}
            pagination={false}
            dataSource={disks ?? []}
            columns={[
              { title: "名称", dataIndex: "name" },
              { title: "容量", render: (_, r) => formatSizeGb(r.size_gb) },
              {
                title: "状态",
                render: (_, r) => (
                  <Space>
                    <DiskStatusBadge status={r.status} />
                    {r.status === "grace" && (
                      <Tooltip title={copy.diskExpirePolicy}>
                        <Typography.Text type="warning" style={{ fontSize: 12 }}>
                          宽限期(只读)
                        </Typography.Text>
                      </Tooltip>
                    )}
                  </Space>
                ),
              },
              { title: "挂载实例", render: (_, r) => instanceName(r.mounted_instance_id) },
              { title: "创建时间", render: (_, r) => formatDateTime(r.created_at) },
              {
                title: "操作",
                render: (_, r) => {
                  const canExpand = r.status === "active";
                  const canDelete = r.mounted_instance_id == null && r.status !== "deleting";
                  return (
                    <Space>
                      <Button
                        size="small"
                        disabled={!canExpand}
                        title={canExpand ? undefined : "仅正常状态的数据盘可扩容"}
                        onClick={() => {
                          setNewSize(r.size_gb + 50);
                          setExpandTarget(r);
                        }}
                      >
                        扩容
                      </Button>
                      <Button
                        size="small"
                        danger
                        disabled={!canDelete}
                        title={canDelete ? undefined : "挂载中的数据盘不能删除"}
                        onClick={() => setDeleteTarget(r)}
                      >
                        删除
                      </Button>
                    </Space>
                  );
                },
              },
            ]}
          />
        )}
      </Card>

      <Modal
        title="新建数据盘"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createDisk.isPending}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ name: "", size_gb: 100 }}
          onFinish={(v: { name: string; size_gb: number }) => createDisk.mutate(v)}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input maxLength={64} />
          </Form.Item>
          <Form.Item name="size_gb" label="容量(GB)" rules={[{ required: true }]}>
            <InputNumber min={10} max={4096} step={10} style={{ width: 200 }} />
          </Form.Item>
          <Typography.Text type="secondary">
            ¥{DISK_PRICE}/GB·月,按日折算扣费;{copy.dailyCostNote}
          </Typography.Text>
        </Form>
      </Modal>

      <Drawer
        title={`扩容:${expandTarget?.name ?? ""}`}
        open={Boolean(expandTarget)}
        onClose={() => setExpandTarget(null)}
        width={420}
        footer={
          <Button
            type="primary"
            block
            loading={expand.isPending}
            disabled={!expandTarget || newSize <= expandTarget.size_gb}
            onClick={() =>
              expandTarget &&
              expand.mutate({ uuid: expandTarget.uuid, body: { size_gb: newSize } })
            }
          >
            确认扩容到 {formatSizeGb(newSize)}
          </Button>
        }
      >
        {expandTarget && (
          <Space orientation="vertical" size={16} style={{ width: "100%" }}>
            <Alert
              type="info"
              showIcon
              message={`当前 ${formatSizeGb(expandTarget.size_gb)},只支持扩容不支持缩容`}
            />
            <Slider
              min={expandTarget.size_gb}
              max={4096}
              step={10}
              value={newSize}
              onChange={setNewSize}
            />
            <Typography.Text type="secondary">
              扩容后每日费用增加约 ¥
              {(((newSize - expandTarget.size_gb) * DISK_PRICE) / 30).toFixed(2)}
            </Typography.Text>
          </Space>
        )}
      </Drawer>
      <DeleteDiskModal disk={deleteTarget} onClose={() => setDeleteTarget(null)} />
    </Space>
  );
}
