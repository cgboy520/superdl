/**
 * 存储(差异化统一页):挂载全景图 + 数据盘列表(计费快照价/到期回收倒计时/扩容抽屉/多级删除防护)。
 * 盘价与宽限/冻结天数一律来自 /policies(禁止前端硬编码);列表「计费」列显示每盘创建时快照价。
 */

import { type DiskOut } from "@superdl/api-client";
import {
  colorPrimary,
  copy,
  formatDateTime,
  formatDaysLeft,
  formatSizeGb,
  statusColors,
} from "@superdl/ui";
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
  theme,
  Tooltip,
  Typography,
} from "antd";
import { useState } from "react";

import { useCreateDisk, useDeleteDisk, useExpandDisk } from "../api/mutations";
import { useDisks, useInstances, usePolicies } from "../api/queries";
import { DiskStatusBadge } from "../components/common";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/storage")({
  beforeLoad: requireAuth,
  component: StoragePage,
});

function MountOverview({ priceText }: { priceText: string }) {
  const { token } = theme.useToken();
  const seg = (title: string, desc: string, color: string) => (
    <div
      style={{
        flex: 1,
        borderTop: `3px solid ${color}`,
        padding: "8px 12px",
        background: token.colorBgContainer,
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
        {seg("/root/data", `数据盘 · ${priceText} · 独立保留`, colorPrimary)}
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

/** 到期/回收列:active 按日扣费;grace/frozen 用起点 + /policies 天数算天级倒计时 */
function ExpiryCell({
  disk,
  graceDays,
  frozenDays,
}: {
  disk: DiskOut;
  graceDays: number;
  frozenDays: number;
}) {
  if (disk.status === "active") {
    return <Typography.Text type="secondary">按日扣费中</Typography.Text>;
  }
  if (disk.status === "grace") {
    const left = formatDaysLeft(disk.grace_started_at, graceDays);
    return (
      <Tooltip title={copy.diskExpirePolicy}>
        <Typography.Text type="warning">宽限期(只读) · {left ?? "—"}</Typography.Text>
      </Tooltip>
    );
  }
  if (disk.status === "frozen") {
    const left = formatDaysLeft(disk.frozen_started_at, frozenDays);
    return (
      <Tooltip title={copy.diskExpirePolicy}>
        <Typography.Text type="danger">冻结 · {left ?? "—"}(到期清除)</Typography.Text>
      </Tooltip>
    );
  }
  if (disk.status === "deleting") {
    return <Typography.Text type="secondary">清除中</Typography.Text>;
  }
  return <span>—</span>;
}

function StoragePage() {
  const { message } = App.useApp();
  const { data: disks, isLoading } = useDisks({ refetchInterval: 10_000 });
  const { data: instances } = useInstances();
  const { data: policies } = usePolicies();
  const [createOpen, setCreateOpen] = useState(false);
  const [expandTarget, setExpandTarget] = useState<DiskOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DiskOut | null>(null);
  const [newSize, setNewSize] = useState(100);
  const [form] = Form.useForm();

  const priceText = policies ? `¥${policies.disk_price_gb_month}/GB·月` : "按日折算计费";
  const graceDays = policies?.disk_grace_days ?? 7;
  const frozenDays = policies?.disk_frozen_days ?? 30;
  const maxGb = policies?.disk_max_gb ?? 4096;

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
    id == null ? "—" : ((instances ?? []).find((i) => i.id === id)?.name ?? `#${id}`);

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
      <MountOverview priceText={priceText} />
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
                title: "计费",
                render: (_, r) => (
                  <Space orientation="vertical" size={0}>
                    <span>¥{r.price_gb_month}/GB·月</span>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      约 ¥{((r.size_gb * Number(r.price_gb_month)) / 30).toFixed(2)}/日
                    </Typography.Text>
                  </Space>
                ),
              },
              { title: "状态", render: (_, r) => <DiskStatusBadge status={r.status} /> },
              {
                title: "到期 / 回收",
                render: (_, r) => (
                  <ExpiryCell disk={r} graceDays={graceDays} frozenDays={frozenDays} />
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
                      <Tooltip title={canExpand ? undefined : "仅正常状态的数据盘可扩容"}>
                        <Button
                          size="small"
                          disabled={!canExpand}
                          onClick={() => {
                            setNewSize(r.size_gb + 50);
                            setExpandTarget(r);
                          }}
                        >
                          扩容
                        </Button>
                      </Tooltip>
                      <Tooltip title={canDelete ? undefined : "挂载中的数据盘不能删除"}>
                        <Button
                          size="small"
                          danger
                          disabled={!canDelete}
                          onClick={() => setDeleteTarget(r)}
                        >
                          删除
                        </Button>
                      </Tooltip>
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
            <InputNumber
              min={policies?.disk_min_gb ?? 10}
              max={maxGb}
              step={10}
              style={{ width: 200 }}
            />
          </Form.Item>
          <Typography.Text type="secondary">
            {priceText},按日折算扣费;{copy.dailyCostNote}
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
              expandTarget && expand.mutate({ uuid: expandTarget.uuid, body: { size_gb: newSize } })
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
              max={maxGb}
              step={10}
              value={newSize}
              onChange={setNewSize}
            />
            <Typography.Text type="secondary">
              扩容后每日费用增加约 ¥
              {(((newSize - expandTarget.size_gb) * Number(expandTarget.price_gb_month)) / 30).toFixed(2)}
              (按本盘快照价)
            </Typography.Text>
          </Space>
        )}
      </Drawer>
      <DeleteDiskModal disk={deleteTarget} onClose={() => setDeleteTarget(null)} />
    </Space>
  );
}
