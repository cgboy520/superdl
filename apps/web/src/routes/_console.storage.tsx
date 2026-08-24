/**
 * 存储(差异化统一页):挂载全景图 + 数据盘列表(计费快照价/到期回收倒计时/扩容抽屉/多级删除防护)。
 * 盘价与宽限/冻结天数一律来自 /policies(禁止前端硬编码);列表「计费」列显示每盘创建时快照价。
 */

import { type DiskOut } from "@superdl/api-client";
import { colorPrimary, diskDailyEstimate, formatDateTime, formatSizeGb, statusColors } from "@superdl/ui";
import { createFileRoute } from "@tanstack/react-router";
import { Trans, useTranslation } from "react-i18next";
import {
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Form,
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

import { useFormat } from "../lib/format";
import { useCreateDisk, useDeleteDisk, useExpandDisk } from "../api/mutations";
import { useDisks, useInstances, usePolicies } from "../api/queries";
import { DiskStatusBadge } from "../components/common";
import { TableErrorEmpty } from "../components/QueryState";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/storage")({
  beforeLoad: requireAuth,
  component: StoragePage,
});

function MountOverview({ priceText }: { priceText: string }) {
  const { t } = useTranslation();
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
    <Card size="small" title={t("storage.mountOverviewTitle")}>
      {/* 只列真实存在的挂载点:后端 Pod 只挂实例盘与数据盘 */}
      <div style={{ display: "flex", gap: 8 }}>
        {seg("/", t("storage.segRoot"), statusColors.blue)}
        {seg("/root/data", t("storage.segData", { price: priceText }), colorPrimary)}
      </div>
    </Card>
  );
}

function DeleteDiskModal({ disk, onClose }: { disk: DiskOut | null; onClose: () => void }) {
  const { t } = useTranslation();
  // 破坏确认:键入资源名才解锁删除
  const [typed, setTyped] = useState("");
  const { message } = App.useApp();
  const del = useDeleteDisk({
    onSuccess: () => {
      message.success(t("storage.deleteStarted"));
      onClose();
    },
  });
  const nameMatched = disk != null && typed.trim() === disk.name;
  return (
    <Modal
      title={t("storage.deleteModalTitle")}
      open={Boolean(disk)}
      onCancel={() => {
        setTyped("");
        onClose();
      }}
      footer={
        <Button
          danger
          type="primary"
          disabled={!nameMatched}
          loading={del.isPending}
          onClick={() => disk && del.mutate(disk.uuid)}
        >
          {t("storage.confirmDelete")}
        </Button>
      }
    >
      <Typography.Paragraph>
        <Trans
          i18nKey="storage.deleteBody"
          values={{ name: disk?.name ?? "", size: formatSizeGb(disk?.size_gb ?? 0) }}
          components={{ b: <Typography.Text strong /> }}
        />
      </Typography.Paragraph>
      <Space orientation="vertical" size={8} style={{ width: "100%" }}>
        <Typography.Text type="secondary">
          {t("storage.typeNameToConfirm", { name: disk?.name ?? "" })}
        </Typography.Text>
        <Input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={disk?.name ?? ""}
          maxLength={64}
          autoComplete="off"
          aria-label={t("storage.typeNameToConfirm", { name: disk?.name ?? "" })}
        />
      </Space>
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
  graceDays: number | undefined;
  frozenDays: number | undefined;
}) {
  const { formatDaysLeft } = useFormat();
  const { t } = useTranslation();
  // 宽限/冻结天数读 /policies;未就绪用无数字兜底句,绝不渲染占位符或硬编码数字
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
  const { data: disks, isLoading, isError, refetch } = useDisks({ refetchInterval: 10_000 });
  const { data: instances } = useInstances();
  const { data: policies } = usePolicies();
  const [createOpen, setCreateOpen] = useState(false);
  const [expandTarget, setExpandTarget] = useState<DiskOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DiskOut | null>(null);
  const [newSize, setNewSize] = useState(100);
  const [form] = Form.useForm();
  const sizeWatch = Form.useWatch<number>("size_gb", form);
  const { formatMoney } = useFormat();

  const priceText = policies ? t("common.gbMonthPrice", { price: policies.disk_price_gb_month }) : t("storage.priceFallback");
  const graceDays = policies?.disk_grace_days;
  const frozenDays = policies?.disk_frozen_days;

  const [diskKeys] = useState(() => new Map<string, string>());
  const createDisk = useCreateDisk({
    onSuccess: () => {
      message.success(t("storage.created"));
      setCreateOpen(false);
      form.resetFields();
      diskKeys.clear(); // 建成了才作废这批键,下一块盘重新分配
    },
  });
  // 幂等键按「盘名 + 容量」派生:响应丢失后重提不会多出一块盘;
  // 改了参数即另一块盘,键随之改变
  const diskIdempotencyKey = (name: string, sizeGb: number): string => {
    const seed = `${name}|${sizeGb}`;
    let k = diskKeys.get(seed);
    if (k === undefined) {
      k = crypto.randomUUID();
      diskKeys.set(seed, k);
    }
    return k;
  };
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
          <TableErrorEmpty onRetry={() => void refetch()} />
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
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {t("common.dailyApprox", { amount: diskDailyEstimate(r.price_gb_month, r.size_gb) })}
                    </Typography.Text>
                  </Space>
                ),
              },
              { title: t("storage.colStatus"), render: (_, r) => (
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
              ) },
              {
                title: t("storage.colExpiry"),
                render: (_, r) => (
                  <ExpiryCell disk={r} graceDays={graceDays} frozenDays={frozenDays} />
                ),
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
                            setNewSize(r.size_gb + 50);
                            setExpandTarget(r);
                          }}
                        >
                          {t("storage.expand")}
                        </Button>
                      </Tooltip>
                      <Tooltip title={canDelete ? undefined : t("storage.deleteNeedsUnmounted")}>
                        <Button
                          size="small"
                          danger
                          disabled={!canDelete}
                          onClick={() => setDeleteTarget(r)}
                        >
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
              idempotencyKey: diskIdempotencyKey(v.name, v.size_gb),
            })
          }
        >
          <Form.Item name="name" label={t("storage.nameLabel")} rules={[{ required: true, message: t("storage.nameRequired") }]}>
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
          <Typography.Text type="secondary">
            {t("storage.createNote", { price: priceText })}
          </Typography.Text>
          {/* 容量对应的日费实时折算 */}
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
