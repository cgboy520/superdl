/** 存储:挂载全景图 + 数据盘列表(计费快照价 / 到期回收倒计时 / 扩容抽屉 / 多级删除防护)。盘价与宽限/冻结天数来自 /policies;「计费」列显示每盘创建时快照价。 */

import { POLL, useAutoRefresh } from "@superdl/ui";
import { type DiskOut } from "@superdl/api-client";
import {
  diskDailyEstimate,
  diskStatusMap,
  drawerWidth,
  fontSize,
  formatDateTime,
  formatSizeGb,
  idemKeyOf,
  space,
  statusColors,
  useThemeColors,
} from "@superdl/ui";
import {
  DiskSizeField,
  GatedButton,
  PageContainer,
  StatusTag,
  TableErrorEmpty,
  TypeConfirmModal,
} from "@superdl/ui/components";
import { createFileRoute } from "@tanstack/react-router";
import { Trans, useTranslation } from "react-i18next";
import {
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Grid,
  Input,
  Modal,
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
import { defaultDiskName } from "../components/create/DataDiskCard";
import { Field } from "../components/Field";
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
  const colors = useThemeColors();
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
        {seg("/root/data", t("storage.segData", { price: priceText }), colors.primary)}
      </div>
    </Card>
  );
}

export function DeleteDiskModal({ disk, onClose }: { disk: DiskOut | null; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const del = useDeleteDisk({
    onSuccess: () => {
      message.success(t("storage.deleteStarted"));
      onClose();
    },
  });
  // 两道闸:键入盘名 + 勾选数据清除(ui-ux-spec §1 规则 7);取消键复用 create.cancel
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
      checkboxLabel={t("storage.ackDataWipe")}
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
  const auto = useAutoRefresh(POLL.steady);
  const {
    data: disks,
    isLoading,
    isError,
    refetch,
    isRefetching,
    dataUpdatedAt,
  } = useDisks({
    refetchInterval: auto.refetchInterval,
  });
  const { data: instances } = useInstances();
  const { data: policies } = usePolicies();
  const [createOpen, setCreateOpen] = useState(false);
  const [expandTarget, setExpandTarget] = useState<DiskOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DiskOut | null>(null);
  const [newSize, setNewSize] = useState(100);
  // 新建盘:名称可选(空则自动生成),容量初值取策略下限
  const [createName, setCreateName] = useState("");
  const [createSize, setCreateSize] = useState<number>();
  const createSizeValue = createSize ?? policies?.disk_min_gb ?? 100;

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

  const openCreate = () => {
    setCreateName("");
    setCreateSize(undefined);
    setCreateOpen(true);
  };
  const submitCreate = () => {
    const name = createName.trim();
    createDisk.mutate({
      body: { name: name || defaultDiskName(), size_gb: createSizeValue },
      idempotencyKey: idemKeyOf("disk", [submitSeq, name, createSizeValue]),
    });
  };

  return (
    <PageContainer
      title={t("storage.title")}
      extra={
        <Button type="primary" onClick={openCreate}>
          {t("create.diskNew")}
        </Button>
      }
      freshness={{
        updatedAt: dataUpdatedAt,
        intervalMs: auto.intervalMs,
        paused: auto.paused,
        onTogglePause: auto.toggle,
        onRefresh: () => void refetch(),
        refreshing: isRefetching,
      }}
    >
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        <MountOverview priceText={priceText} />
        <Card>
          {isError ? (
            <TableErrorEmpty isError onRetry={() => void refetch()} />
          ) : (disks ?? []).length === 0 && !isLoading ? (
            <Empty description={t("copy.diskRetention")}>
              <Button type="primary" onClick={openCreate}>
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
                    <Space size={space.xs}>
                      <StatusTag map={diskStatusMap} value={r.status} variant="badge" />
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
                        <GatedButton
                          size="small"
                          reason={canExpand ? undefined : t("storage.expandNeedsActive")}
                          onClick={() => {
                            setNewSize(r.size_gb + EXPAND_DEFAULT_STEP_GB);
                            setExpandTarget(r);
                          }}
                        >
                          {t("storage.expand")}
                        </GatedButton>
                        <GatedButton
                          size="small"
                          danger
                          reason={canDelete ? undefined : t("storage.deleteNeedsUnmounted")}
                          onClick={() => setDeleteTarget(r)}
                        >
                          {t("storage.delete")}
                        </GatedButton>
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
          onOk={submitCreate}
          confirmLoading={createDisk.isPending}
        >
          <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
            <Field label={t("storage.nameLabel")}>
              <Input
                maxLength={64}
                aria-label={t("storage.nameLabel")}
                placeholder={t("create.namePlaceholder")}
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                onPressEnter={submitCreate}
              />
            </Field>
            <Field label={t("storage.sizeLabel")}>
              <DiskSizeField
                value={createSizeValue}
                onChange={setCreateSize}
                min={policies?.disk_min_gb}
                max={policies?.disk_max_gb}
                priceGbMonth={policies?.disk_price_gb_month}
                ariaLabel={t("create.diskSizeAria")}
              />
            </Field>
            <Typography.Text type="secondary">{t("storage.createNote", { price: priceText })}</Typography.Text>
          </Space>
        </Modal>

        <Drawer
          title={t("storage.expandDrawerTitle", { name: expandTarget?.name ?? "" })}
          open={Boolean(expandTarget)}
          onClose={() => setExpandTarget(null)}
          size={drawerWidth.md}
          footer={
            <Space style={{ width: "100%", justifyContent: "flex-end" }}>
              <Button onClick={() => setExpandTarget(null)}>{t("create.cancel")}</Button>
              <Button
                type="primary"
                loading={expand.isPending}
                disabled={!expandTarget || newSize <= expandTarget.size_gb}
                onClick={() => expandTarget && expand.mutate({ uuid: expandTarget.uuid, body: { size_gb: newSize } })}
              >
                {t("storage.confirmExpandTo", { size: formatSizeGb(newSize) })}
              </Button>
            </Space>
          }
        >
          {expandTarget && policies && (
            <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
              {/* 基线 = 当前容量,估算只算新增部分;差价按本盘快照价 */}
              <DiskSizeField
                value={newSize}
                onChange={setNewSize}
                min={policies.disk_min_gb}
                max={policies.disk_max_gb}
                baseline={expandTarget.size_gb}
                priceGbMonth={expandTarget.price_gb_month}
                ariaLabel={t("create.diskSizeAria")}
              />
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {t("storage.expandSnapshotPrice", {
                  price: t("common.gbMonthPrice", { price: expandTarget.price_gb_month }),
                })}
              </Typography.Text>
            </Space>
          )}
        </Drawer>
        <DeleteDiskModal disk={deleteTarget} onClose={() => setDeleteTarget(null)} />
      </Space>
    </PageContainer>
  );
}
