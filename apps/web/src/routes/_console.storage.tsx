/** 存储:挂载全景图 + 数据盘列表(计费快照价 / 到期回收倒计时 / 扩容抽屉 / 多级删除防护)。盘价与宽限/冻结天数来自 /policies;「计费」列显示每盘创建时快照价。 */

import { POLL } from "@superdl/ui";
import { type DiskOut } from "@superdl/api-client";
import {
  colorPrimary,
  diskDailyEstimate,
  fontSize,
  formatDateTime,
  formatSizeGb,
  idemKeyOf,
  statusColors,
} from "@superdl/ui";
import { TableErrorEmpty, TypeConfirmModal } from "@superdl/ui/components";
import { createFileRoute } from "@tanstack/react-router";
import { Trans, useTranslation } from "react-i18next";
import {
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Form,
  Grid,
  Input,
  InputNumber,
  Modal,
  Slider,
  Space,
  Table,
  Tag,
  theme,
  Tooltip,
  Typography,
} from "antd";
import { useState } from "react";

import { useFormat } from "@superdl/ui";
import { useCreateDisk, useDeleteDisk, useExpandDisk } from "../api/mutations";
import { useDisks, useInstances, usePolicies } from "../api/queries";
import { DiskStatusBadge } from "../components/common";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/storage")({
  beforeLoad: requireAuth,
  component: StoragePage,
});

/** 扩容抽屉默认步进(GB):默认目标 = 当前 +50;滑块可自由调。 */
const EXPAND_DEFAULT_STEP_GB = 50;

function MountOverview({ priceText }: { priceText: string }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  // md 以下改竖排
  const screens = Grid.useBreakpoint();
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
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {desc}
      </Typography.Text>
    </div>
  );
  return (
    <Card size="small" title={t("storage.mountOverviewTitle")}>
      <div style={{ display: "flex", gap: 8, flexDirection: screens.md ? "row" : "column" }}>
        {seg("/root", t("storage.segRoot"), statusColors.blue)}
        {seg("/root/data", t("storage.segData", { price: priceText }), colorPrimary)}
      </div>
    </Card>
  );
}

function DeleteDiskModal({ disk, onClose }: { disk: DiskOut | null; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const del = useDeleteDisk({
    onSuccess: () => {
      message.success(t("storage.deleteStarted"));
      onClose();
    },
  });
  // 破坏确认只有键入盘名一道闸;取消键复用 create.cancel
  return (
    <TypeConfirmModal
      open={Boolean(disk)}
      title={t("storage.deleteModalTitle")}
      body={
        <Trans
          i18nKey="storage.deleteBody"
          values={{ name: disk?.name ?? "", size: formatSizeGb(disk?.size_gb ?? 0) }}
          components={{ b: <Typography.Text strong /> }}
        />
      }
      targetName={disk?.name ?? ""}
      confirmLabel={t("storage.confirmDelete")}
      cancelLabel={t("create.cancel")}
      loading={del.isPending}
      onConfirm={() => disk && del.mutate(disk.uuid)}
      onCancel={onClose}
    />
  );
}

/** 到期/回收列:active 按日扣费;grace/frozen 用起点 + /policies 天数算倒计时 */
function ExpiryCell({
  disk,
  graceDays,
  frozenDays,
}: {
  disk: DiskOut;
  graceDays: number | undefined;
  frozenDays: number | undefined;
}) {
  const { formatDaysLeft } = useFormat();
  const { t } = useTranslation();
  // 宽限/冻结天数读 /policies;未就绪用无数字兜底句
  const policyTip =
    graceDays != null && frozenDays != null
      ? t("copy.diskExpirePolicy", { graceDays, frozenDays })
      : t("copy.diskExpirePolicyFallback");
  if (disk.status === "active") {
    return <Typography.Text type="secondary">{t("storage.activeBilling")}</Typography.Text>;
  }
  if (disk.status === "grace") {
    const left = graceDays == null ? null : formatDaysLeft(disk.grace_started_at, graceDays);
    return (
      <Tooltip title={policyTip}>
        <Typography.Text type="warning">{t("storage.graceLine", { left: left ?? "—" })}</Typography.Text>
      </Tooltip>
    );
  }
  if (disk.status === "frozen") {
    const left = frozenDays == null ? null : formatDaysLeft(disk.frozen_started_at, frozenDays);
    return (
      <Tooltip title={policyTip}>
        <Typography.Text type="danger">{t("storage.frozenLine", { left: left ?? "—" })}</Typography.Text>
      </Tooltip>
    );
  }
  if (disk.status === "deleting") {
    return <Typography.Text type="secondary">{t("storage.deletingLabel")}</Typography.Text>;
  }
  return <span>—</span>;
}

function StoragePage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  // 数据盘状态由欠费巡检驱动(小时级):稳态 30s 单档
  const { data: disks, isLoading, isError, refetch } = useDisks({ refetchInterval: POLL.steady });
  const { data: instances } = useInstances();
  const { data: policies } = usePolicies();
  const [createOpen, setCreateOpen] = useState(false);
  const [expandTarget, setExpandTarget] = useState<DiskOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DiskOut | null>(null);
  const [newSize, setNewSize] = useState(100);
  const [form] = Form.useForm();
  const sizeWatch = Form.useWatch<number | undefined>("size_gb", form);
  const { formatMoney } = useFormat();

  const priceText = policies
    ? t("common.gbMonthPrice", { price: policies.disk_price_gb_month })
    : t("storage.priceFallback");
  const graceDays = policies?.disk_grace_days;
  const frozenDays = policies?.disk_frozen_days;

  // 幂等键按「提交序号 + 盘名 + 容量」派生;建成才递增序号
  const [submitSeq, setSubmitSeq] = useState(0);
  const createDisk = useCreateDisk({
    onSuccess: () => {
      message.success(t("storage.created"));
      setCreateOpen(false);
      form.resetFields();
      setSubmitSeq((s) => s + 1);
    },
  });
  const expand = useExpandDisk({
    onSuccess: () => {
      message.success(t("storage.expanded"));
      setExpandTarget(null);
    },
  });

  const instanceName = (id: number | null) =>
    id == null ? "—" : ((instances ?? []).find((i) => i.id === id)?.name ?? `#${id}`);

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Space style={{ width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {t("storage.title")}
        </Typography.Title>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          {t("create.diskNew")}
        </Button>
      </Space>
      <MountOverview priceText={priceText} />
      <Card>
        {isError ? (
          <TableErrorEmpty isError onRetry={() => void refetch()} />
        ) : (disks ?? []).length === 0 && !isLoading ? (
          <Empty description={t("copy.diskRetention")}>
            <Button type="primary" onClick={() => setCreateOpen(true)}>
              {t("storage.createFirst")}
            </Button>
          </Empty>
        ) : (
          <Table<DiskOut>
            rowKey="uuid"
            loading={isLoading}
            pagination={false}
            scroll={{ x: 920 }}
            dataSource={disks ?? []}
            columns={[
              { title: t("storage.nameLabel"), dataIndex: "name" },
              { title: t("storage.colSize"), render: (_, r) => formatSizeGb(r.size_gb) },
              {
                title: t("storage.colBilling"),
                render: (_, r) => (
                  <Space orientation="vertical" size={0}>
                    <span>{t("common.gbMonthPrice", { price: r.price_gb_month })}</span>
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                      {t("common.dailyApprox", { amount: diskDailyEstimate(r.price_gb_month, r.size_gb) })}
                    </Typography.Text>
                  </Space>
                ),
              },
              {
                title: t("storage.colStatus"),
                render: (_, r) => (
                  <Space size={4}>
                    <DiskStatusBadge status={r.status} />
                    {!r.quota_synced && r.status !== "deleting" && (
                      <Tooltip title={t("storage.quotaPendingHint")}>
                        <Tag color="gold" style={{ marginInlineEnd: 0 }}>
                          {t("storage.quotaPending")}
                        </Tag>
                      </Tooltip>
                    )}
                  </Space>
                ),
              },
              {
                title: t("storage.colExpiry"),
                render: (_, r) => <ExpiryCell disk={r} graceDays={graceDays} frozenDays={frozenDays} />,
              },
              { title: t("storage.colMounted"), render: (_, r) => instanceName(r.mounted_instance_id) },
              { title: t("storage.colCreated"), render: (_, r) => formatDateTime(r.created_at) },
              {
                title: t("storage.colActions"),
                render: (_, r) => {
                  const canExpand = r.status === "active";
                  const canDelete = r.mounted_instance_id == null && r.status !== "deleting";
                  return (
                    <Space>
                      <Tooltip title={canExpand ? undefined : t("storage.expandNeedsActive")}>
                        <Button
                          size="small"
                          disabled={!canExpand}
                          onClick={() => {
                            setNewSize(r.size_gb + EXPAND_DEFAULT_STEP_GB);
                            setExpandTarget(r);
                          }}
                        >
                          {t("storage.expand")}
                        </Button>
                      </Tooltip>
                      <Tooltip title={canDelete ? undefined : t("storage.deleteNeedsUnmounted")}>
                        <Button size="small" danger disabled={!canDelete} onClick={() => setDeleteTarget(r)}>
                          {t("storage.delete")}
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
        title={t("create.diskNew")}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createDisk.isPending}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ name: "", size_gb: 100 }}
          onFinish={(v: { name: string; size_gb: number }) =>
            createDisk.mutate({
              body: v,
              idempotencyKey: idemKeyOf("disk", [submitSeq, v.name, v.size_gb]),
            })
          }
        >
          <Form.Item
            name="name"
            label={t("storage.nameLabel")}
            rules={[{ required: true, message: t("storage.nameRequired") }]}
          >
            <Input maxLength={64} />
          </Form.Item>
          <Form.Item name="size_gb" label={t("storage.sizeLabel")} rules={[{ required: true }]}>
            <InputNumber
              min={policies?.disk_min_gb}
              max={policies?.disk_max_gb}
              step={10}
              disabled={!policies}
              style={{ width: 200 }}
            />
          </Form.Item>
          <Typography.Text type="secondary">{t("storage.createNote", { price: priceText })}</Typography.Text>
          <Typography.Text strong style={{ display: "block", marginTop: 8 }}>
            {t("storage.dailyEstimate", {
              size: sizeWatch ?? 0,
              amount: formatMoney(diskDailyEstimate(policies?.disk_price_gb_month, sizeWatch ?? 0)),
            })}
          </Typography.Text>
        </Form>
      </Modal>

      <Drawer
        title={t("storage.expandDrawerTitle", { name: expandTarget?.name ?? "" })}
        open={Boolean(expandTarget)}
        onClose={() => setExpandTarget(null)}
        size="min(420px, 100vw)"
        footer={
          <Button
            type="primary"
            block
            loading={expand.isPending}
            disabled={!expandTarget || newSize <= expandTarget.size_gb}
            onClick={() => expandTarget && expand.mutate({ uuid: expandTarget.uuid, body: { size_gb: newSize } })}
          >
            {t("storage.confirmExpandTo", { size: formatSizeGb(newSize) })}
          </Button>
        }
      >
        {expandTarget && policies && (
          <Space orientation="vertical" size={16} style={{ width: "100%" }}>
            <Slider
              min={expandTarget.size_gb}
              max={policies.disk_max_gb}
              step={10}
              value={newSize}
              onChange={setNewSize}
            />
            <Typography.Text type="secondary">
              {t("storage.expandCostNote", {
                daily: t("common.dailyApprox", {
                  amount: diskDailyEstimate(expandTarget.price_gb_month, newSize - expandTarget.size_gb),
                }),
              })}
            </Typography.Text>
          </Space>
        )}
      </Drawer>
      <DeleteDiskModal disk={deleteTarget} onClose={() => setDeleteTarget(null)} />
    </Space>
  );
}
