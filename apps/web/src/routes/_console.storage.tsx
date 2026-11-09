/** Storage: mount layout (system disk at /root, data disks at their mount paths, tenant storage under /mnt/org) + data-disk list (usage, the instances mounting each disk, snapshot price, expiry countdown, expand / move / delete). Every disk lives on the platform's Ceph cluster and mounts on all of the user's instances from their next start; "Add storage" expands the /data disk by default or creates a disk at a chosen path. */

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
  OptionTileGroup,
  PageContainer,
  StatusTag,
  TableErrorEmpty,
  TypeConfirmModal,
} from "@superdl/ui/components";
import { createFileRoute } from "@tanstack/react-router";
import { Trans, useTranslation } from "react-i18next";
import {
  Alert,
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
import { useCreateDisk, useDeleteDisk, useUpdateDisk } from "../api/mutations";
import { useDisks, useMyOrg, useOrgMounts, usePolicies } from "../api/queries";
import { Field } from "../components/Field";
import { UsageCell } from "../components/storage/UsageCell";
import { DEFAULT_MOUNT_PATH, defaultDiskName, isValidMountPath, ORG_MOUNT_ROOT, suggestMountPath } from "../lib/disks";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/storage")({
  beforeLoad: requireAuth,
  component: StoragePage,
});

/** Default step of the expansion controls (GB): default target = current + 50; the slider moves freely. */
const EXPAND_DEFAULT_STEP_GB = 50;
const MOUNTED_LIST_MAX = 3;

const liveDisks = (disks: DiskOut[] | undefined) => (disks ?? []).filter((d) => d.status !== "deleted");

function Segment({ path, desc, color, extra }: { path: string; desc: string; color: string; extra?: React.ReactNode }) {
  const { token } = theme.useToken();
  return (
    <div style={{ flex: 1, borderTop: `3px solid ${color}`, padding: "8px 12px", background: token.colorBgContainer }}>
      <Typography.Text strong code>
        {path}
      </Typography.Text>
      <br />
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {desc}
      </Typography.Text>
      {extra}
    </div>
  );
}

function MountOverview({ priceText, disks }: { priceText: string; disks: DiskOut[] }) {
  const { t } = useTranslation();
  const colors = useThemeColors();
  const screens = Grid.useBreakpoint();
  const { data: org } = useMyOrg();
  const inTenant = Boolean(org?.org);
  const { data: mounts } = useOrgMounts({ enabled: inTenant });
  const orgMounts = (mounts ?? []).filter((m) => m.enabled && m.status === "ready");
  const dataPaths = disks.map((d) => d.mount_path).sort();
  return (
    <Card size="small" title={t("storage.mountOverviewTitle")}>
      <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
        <div style={{ display: "flex", gap: 8, flexDirection: screens.md ? "row" : "column" }}>
          <Segment path="/root" desc={t("storage.segRoot")} color={statusColors.blue} />
          <Segment
            path={dataPaths.length > 1 ? dataPaths.join(" · ") : DEFAULT_MOUNT_PATH}
            desc={t("storage.segData", { price: priceText })}
            color={colors.primary}
          />
          {inTenant && orgMounts.length > 0 && (
            <Segment
              path={`${ORG_MOUNT_ROOT}/*`}
              desc={t("storage.segOrg", { n: orgMounts.length })}
              color={statusColors.purple}
            />
          )}
        </div>
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("copy.storageOnCeph")}
        </Typography.Text>
      </Space>
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
  return (
    <TypeConfirmModal
      open={Boolean(disk)}
      title={t("storage.deleteModalTitle")}
      body={
        <Trans
          i18nKey="storage.deleteBody"
          values={{ name: disk?.name ?? "", size: formatSizeGb(disk?.size_gb ?? 0), path: disk?.mount_path ?? "" }}
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

/** Expiry / reclamation column: active bills daily; grace / frozen count down from the start + /policies days */
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

function MountedCell({ disk }: { disk: DiskOut }) {
  const { t } = useTranslation();
  const users = disk.mounted_by ?? [];
  if (users.length === 0) return <Typography.Text type="secondary">{t("storage.mountedNone")}</Typography.Text>;
  return (
    <Space size={[space.xs, space.xs]} wrap>
      {users.slice(0, MOUNTED_LIST_MAX).map((u) => (
        <Tag key={u.uuid} style={{ marginInlineEnd: 0 }}>
          {u.name}
        </Tag>
      ))}
      {users.length > MOUNTED_LIST_MAX && (
        <Tooltip
          title={users
            .slice(MOUNTED_LIST_MAX)
            .map((u) => u.name)
            .join(", ")}
        >
          <Tag style={{ marginInlineEnd: 0 }}>+{users.length - MOUNTED_LIST_MAX}</Tag>
        </Tooltip>
      )}
    </Space>
  );
}

/** Why a mount path cannot be used (the API's disks.mountPathInvalid / mountPathTaken), checked before submit. */
function useMountPathError(path: string, disks: DiskOut[], selfUuid: string | null): string | null {
  const { t } = useTranslation();
  if (!isValidMountPath(path)) return t("storage.mountPathInvalid");
  if (disks.some((d) => d.uuid !== selfUuid && d.mount_path === path)) return t("storage.mountPathTaken", { path });
  return null;
}

type AddMode = "expand" | "new";

/** "Add storage": expand the /data disk (default when one exists) or create a disk at a chosen mount path. */
function AddStorageModal({ open, disks, onClose }: { open: boolean; disks: DiskOut[]; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { data: policies } = usePolicies();
  const dataDisk = disks.find((d) => d.mount_path === DEFAULT_MOUNT_PATH) ?? null;
  const [mode, setMode] = useState<AddMode>(dataDisk ? "expand" : "new");
  const [targetGb, setTargetGb] = useState((dataDisk?.size_gb ?? 0) + EXPAND_DEFAULT_STEP_GB);
  const [name, setName] = useState("");
  const [sizeGb, setSizeGb] = useState<number>();
  const [path, setPath] = useState(() => suggestMountPath(disks.map((d) => d.mount_path)));
  const [seq, setSeq] = useState(0);
  const sizeValue = sizeGb ?? policies?.disk_min_gb ?? 100;
  const newPathError = useMountPathError(path.trim(), disks, null);
  const pathError = mode === "new" ? newPathError : null;
  const create = useCreateDisk({
    onSuccess: () => {
      message.success(t("storage.created"));
      setSeq((s) => s + 1);
      onClose();
    },
  });
  const update = useUpdateDisk({
    onSuccess: () => {
      message.success(t("storage.expanded"));
      onClose();
    },
  });
  const expandReason = !dataDisk
    ? t("storage.addExpandNone")
    : dataDisk.status !== "active"
      ? t("storage.expandNeedsActive")
      : undefined;
  const submit = () => {
    if (mode === "expand" && dataDisk) {
      update.mutate({ uuid: dataDisk.uuid, body: { size_gb: targetGb } });
      return;
    }
    if (pathError) return;
    const trimmed = name.trim();
    create.mutate({
      body: { name: trimmed || defaultDiskName(), size_gb: sizeValue, mount_path: path.trim() },
      idempotencyKey: idemKeyOf("disk", [seq, trimmed, sizeValue, path.trim()]),
    });
  };
  const okDisabled =
    mode === "expand" ? Boolean(expandReason) || !dataDisk || targetGb <= dataDisk.size_gb : Boolean(pathError);
  return (
    <Modal
      title={t("storage.addModalTitle")}
      open={open}
      onCancel={onClose}
      onOk={submit}
      okText={
        mode === "expand" ? t("storage.confirmExpandTo", { size: formatSizeGb(targetGb) }) : t("storage.confirmCreate")
      }
      okButtonProps={{ disabled: okDisabled }}
      confirmLoading={create.isPending || update.isPending}
      width={640}
      destroyOnHidden
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <OptionTileGroup<AddMode>
          label={t("storage.addModalTitle")}
          hideLabel
          columns={2}
          size="sm"
          value={mode}
          onChange={setMode}
          options={[
            {
              value: "expand",
              title: t("storage.addExpand", { path: DEFAULT_MOUNT_PATH }),
              description: dataDisk
                ? t("storage.addExpandDesc", { name: dataDisk.name, size: formatSizeGb(dataDisk.size_gb) })
                : t("storage.addExpandNone"),
              reason: expandReason,
            },
            { value: "new", title: t("storage.addNew"), description: t("storage.addNewDesc") },
          ]}
        />
        {mode === "expand" && dataDisk && policies && (
          <>
            <DiskSizeField
              value={targetGb}
              onChange={setTargetGb}
              min={policies.disk_min_gb}
              max={policies.disk_max_gb}
              baseline={dataDisk.size_gb}
              priceGbMonth={dataDisk.price_gb_month}
              ariaLabel={t("create.diskSizeAria")}
            />
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {t("storage.expandOnline")}
            </Typography.Text>
          </>
        )}
        {mode === "new" && (
          <>
            <Field label={t("storage.nameLabel")}>
              <Input
                maxLength={64}
                aria-label={t("storage.nameLabel")}
                placeholder={t("create.namePlaceholder")}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <Field label={t("storage.mountPathLabel")} error={pathError} hint={t("storage.mountPathHint")}>
              <Input
                maxLength={70}
                aria-label={t("storage.mountPathLabel")}
                value={path}
                onChange={(e) => setPath(e.target.value)}
                onPressEnter={submit}
              />
            </Field>
            <Field label={t("storage.sizeLabel")}>
              <DiskSizeField
                value={sizeValue}
                onChange={setSizeGb}
                min={policies?.disk_min_gb}
                max={policies?.disk_max_gb}
                priceGbMonth={policies?.disk_price_gb_month}
                ariaLabel={t("create.diskSizeAria")}
              />
            </Field>
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {t("storage.newDiskNextStart")}
            </Typography.Text>
          </>
        )}
      </Space>
    </Modal>
  );
}

function MountPathModal({ disk, disks, onClose }: { disk: DiskOut | null; disks: DiskOut[]; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [path, setPath] = useState(disk?.mount_path ?? "");
  const error = useMountPathError(path.trim(), disks, disk?.uuid ?? null);
  const update = useUpdateDisk({
    onSuccess: () => {
      message.success(t("storage.pathChanged"));
      onClose();
    },
  });
  return (
    <Modal
      title={t("storage.changePathTitle", { name: disk?.name ?? "" })}
      open={Boolean(disk)}
      onCancel={onClose}
      onOk={() => disk && !error && update.mutate({ uuid: disk.uuid, body: { mount_path: path.trim() } })}
      okButtonProps={{ disabled: Boolean(error) || path.trim() === disk?.mount_path }}
      confirmLoading={update.isPending}
      destroyOnHidden
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Field label={t("storage.mountPathLabel")} error={error} hint={t("storage.mountPathHint")}>
          <Input
            maxLength={70}
            aria-label={t("storage.mountPathLabel")}
            value={path}
            onChange={(e) => setPath(e.target.value)}
          />
        </Field>
        <Alert type="info" showIcon title={t("storage.pathNextStart")} />
      </Space>
    </Modal>
  );
}

function StoragePage() {
  const { t } = useTranslation();
  const { formatMoney, formatPrice, minorUnits } = useFormat();
  const { message } = App.useApp();
  const auto = useAutoRefresh(POLL.steady);
  const {
    data: disksData,
    isLoading,
    isError,
    refetch,
    isRefetching,
    dataUpdatedAt,
  } = useDisks({
    refetchInterval: auto.refetchInterval,
  });
  const disks = liveDisks(disksData);
  const { data: policies } = usePolicies();
  const [addOpen, setAddOpen] = useState(false);
  const [expandTarget, setExpandTarget] = useState<DiskOut | null>(null);
  const [pathTarget, setPathTarget] = useState<DiskOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DiskOut | null>(null);
  const [newSize, setNewSize] = useState(100);

  const priceText = policies
    ? t("common.gbMonthPrice", { price: formatPrice(policies.disk_price_gb_month) })
    : t("storage.priceFallback");
  const graceDays = policies?.disk_grace_days;
  const frozenDays = policies?.disk_frozen_days;

  const expand = useUpdateDisk({
    onSuccess: () => {
      message.success(t("storage.expanded"));
      setExpandTarget(null);
    },
  });

  return (
    <PageContainer
      title={t("storage.title")}
      extra={
        <Button type="primary" onClick={() => setAddOpen(true)}>
          {t("storage.addStorage")}
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
        <MountOverview priceText={priceText} disks={disks} />
        <Card>
          {isError ? (
            <TableErrorEmpty isError onRetry={() => void refetch()} />
          ) : disks.length === 0 && !isLoading ? (
            <Empty description={t("copy.diskRetention")}>
              <Button type="primary" onClick={() => setAddOpen(true)}>
                {t("storage.createFirst")}
              </Button>
            </Empty>
          ) : (
            <Table<DiskOut>
              rowKey="uuid"
              loading={isLoading}
              pagination={false}
              scroll={{ x: 1080 }}
              dataSource={disks}
              columns={[
                { title: t("storage.nameLabel"), dataIndex: "name" },
                {
                  title: t("storage.colMountPath"),
                  render: (_, r) => <Typography.Text code>{r.mount_path}</Typography.Text>,
                },
                {
                  title: t("storage.colUsage"),
                  render: (_, r) => (
                    <UsageCell usedBytes={r.bytes_used} sizeGb={r.size_gb} checkedAt={r.usage_checked_at} />
                  ),
                },
                {
                  title: t("storage.colBilling"),
                  render: (_, r) => (
                    <Space orientation="vertical" size={0}>
                      <span>{t("common.gbMonthPrice", { price: formatPrice(r.price_gb_month) })}</span>
                      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                        {t("common.dailyApprox", {
                          amount: formatMoney(diskDailyEstimate(r.price_gb_month, r.size_gb, minorUnits === 0 ? 0 : 2)),
                        })}
                      </Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: t("storage.colStatus"),
                  render: (_, r) => (
                    <Space size={space.xs}>
                      <StatusTag map={diskStatusMap} value={r.status} variant="badge" />
                      {!r.provisioned && r.status !== "deleting" && (
                        <Tooltip title={t("storage.provisioningHint")}>
                          <Tag color="gold" style={{ marginInlineEnd: 0 }}>
                            {t("storage.provisioning")}
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
                { title: t("storage.colMounted"), render: (_, r) => <MountedCell disk={r} /> },
                { title: t("storage.colCreated"), render: (_, r) => formatDateTime(r.created_at) },
                {
                  title: t("storage.colActions"),
                  render: (_, r) => {
                    const users = r.mounted_by ?? [];
                    const canExpand = r.status === "active";
                    const canMove = r.status !== "deleting";
                    const deleteReason =
                      r.status === "deleting"
                        ? t("storage.deletingLabel")
                        : users.length > 0
                          ? t("storage.deleteNeedsStopped", { names: users.map((u) => u.name).join(", ") })
                          : undefined;
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
                          reason={canMove ? undefined : t("storage.deletingLabel")}
                          onClick={() => setPathTarget(r)}
                        >
                          {t("storage.changePath")}
                        </GatedButton>
                        <GatedButton size="small" danger reason={deleteReason} onClick={() => setDeleteTarget(r)}>
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

        {addOpen && <AddStorageModal open={addOpen} disks={disks} onClose={() => setAddOpen(false)} />}
        {pathTarget && <MountPathModal disk={pathTarget} disks={disks} onClose={() => setPathTarget(null)} />}

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
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {t("storage.expandOnline")}
              </Typography.Text>
            </Space>
          )}
        </Drawer>
        <DeleteDiskModal disk={deleteTarget} onClose={() => setDeleteTarget(null)} />
      </Space>
    </PageContainer>
  );
}
