/** Storage card of the create / deploy pages: what the new workload mounts (system disk at /root, every data disk of the user at its mount path, the tenant's storage under /mnt/org) and, when no disk sits at /data yet, the option to create one on submit. */

import type { DiskOut, OrgMountOut } from "@superdl/api-client";
import { controlWidth, fontSize, formatSizeGb, space } from "@superdl/ui";
import { DataErrorAlert, DiskSizeField, OptionTileGroup } from "@superdl/ui/components";
import { Link } from "@tanstack/react-router";
import { Card, Collapse, Input, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useDisks, useMyOrg, useOrgMounts, usePolicies } from "../../api/queries";
import { DEFAULT_MOUNT_PATH } from "../../lib/disks";

export type DiskMode = "none" | "new";

const MOUNTED = new Set(["active", "grace"]);

export const mountedDisks = (disks: DiskOut[] | undefined) => (disks ?? []).filter((d) => MOUNTED.has(d.status));
/** Whether a disk (in any state but deleted) already holds /data: then the form offers no new one there. */
export const hasDataDisk = (disks: DiskOut[] | undefined) =>
  (disks ?? []).some((d) => d.mount_path === DEFAULT_MOUNT_PATH && d.status !== "deleted");

function MountRow({ path, children }: { path: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: space.sm, alignItems: "baseline", flexWrap: "wrap" }}>
      <Typography.Text code style={{ minWidth: 96 }}>
        {path}
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {children}
      </Typography.Text>
    </div>
  );
}

function OrgMountRows({ mounts }: { mounts: OrgMountOut[] }) {
  const { t } = useTranslation();
  return (
    <>
      {mounts.map((m) => (
        <MountRow key={m.uuid} path={m.mount_path}>
          {t("create.storageOrgMount")}
          {m.read_only && (
            <Tag style={{ marginInlineStart: space.xs }} variant="filled">
              {t("create.storageReadOnly")}
            </Tag>
          )}
        </MountRow>
      ))}
    </>
  );
}

export function StorageCard({
  mode,
  onModeChange,
  newName,
  onNewNameChange,
  newGb,
  onNewGbChange,
  systemDiskGb,
  id,
  variant = "card",
}: {
  mode: DiskMode;
  onModeChange: (mode: DiskMode) => void;
  newName: string;
  onNewNameChange: (name: string) => void;
  newGb: number;
  onNewGbChange: (gb: number) => void;
  /** System disk size of the spec; omitted where the workload has none to show. */
  systemDiskGb?: number;
  /** Anchor id (for the incomplete-items list) */
  id?: string;
  /** card = standalone card (create page); section = embedded in another card, title row only (deploy page container section) */
  variant?: "card" | "section";
}) {
  const { t } = useTranslation();
  const disksQ = useDisks();
  const { data: policies } = usePolicies();
  const { data: org } = useMyOrg();
  const { data: orgMounts } = useOrgMounts({ enabled: Boolean(org?.org) });
  const disks = (disksQ.data ?? [])
    .filter((d) => MOUNTED.has(d.status) || d.status === "frozen")
    .sort((a, b) => a.mount_path.localeCompare(b.mount_path));
  const readyOrgMounts = (orgMounts ?? []).filter((m) => m.enabled && m.status === "ready");
  const canCreate = !hasDataDisk(disksQ.data);

  const body = (
    <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
      {disksQ.isError ? (
        <DataErrorAlert onRetry={() => void disksQ.refetch()} />
      ) : (
        <Space orientation="vertical" size={space.xs} style={{ width: "100%" }}>
          {systemDiskGb != null && (
            <MountRow path="/root">{t("create.storageSystemDisk", { size: formatSizeGb(systemDiskGb) })}</MountRow>
          )}
          {disks.map((d) => (
            <MountRow key={d.uuid} path={d.mount_path}>
              {t("create.storageDataDisk", { name: d.name, size: formatSizeGb(d.size_gb) })}
              {d.status === "grace" && (
                <Tag color="orange" style={{ marginInlineStart: space.xs }} variant="filled">
                  {t("create.storageReadOnly")}
                </Tag>
              )}
              {d.status === "frozen" && (
                <Tag color="red" style={{ marginInlineStart: space.xs }} variant="filled">
                  {t("create.storageFrozen")}
                </Tag>
              )}
            </MountRow>
          ))}
          {mode === "new" && canCreate && (
            <MountRow path={DEFAULT_MOUNT_PATH}>{t("create.storageNewDisk", { size: formatSizeGb(newGb) })}</MountRow>
          )}
          <OrgMountRows mounts={readyOrgMounts} />
        </Space>
      )}
      {canCreate && (
        <>
          <OptionTileGroup
            label={t("create.diskCard")}
            hideLabel
            columns={2}
            size="sm"
            value={mode}
            onChange={onModeChange}
            options={[
              { value: "none", title: t("create.diskNone"), description: t("create.diskNoneDesc") },
              {
                value: "new",
                title: t("create.diskNew", { path: DEFAULT_MOUNT_PATH }),
                description: t("create.diskNewDesc"),
              },
            ]}
          />
          {mode === "new" && (
            <>
              <DiskSizeField
                value={newGb}
                onChange={onNewGbChange}
                min={policies?.disk_min_gb}
                max={policies?.disk_max_gb}
                priceGbMonth={policies?.disk_price_gb_month}
                ariaLabel={t("create.diskSizeAria")}
              />
              <Collapse
                ghost
                size="small"
                items={[
                  {
                    key: "advanced",
                    label: t("create.diskAdvanced"),
                    children: (
                      <Space size={space.md} align="center">
                        <Typography.Text type="secondary">{t("storage.nameLabel")}</Typography.Text>
                        <Input
                          style={{ width: controlWidth.md }}
                          maxLength={64}
                          aria-label={t("storage.nameLabel")}
                          value={newName}
                          onChange={(e) => onNewNameChange(e.target.value)}
                        />
                      </Space>
                    ),
                  },
                ]}
              />
            </>
          )}
        </>
      )}
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {t("create.storageAutoMountNote")} <Link to="/storage">{t("create.goStorage")}</Link>
      </Typography.Text>
    </Space>
  );
  if (variant === "section") {
    return (
      <div id={id}>
        <Typography.Text strong style={{ display: "block", marginBottom: space.sm }}>
          {t("create.diskCard")}
        </Typography.Text>
        {body}
      </div>
    );
  }
  return (
    <Card id={id} title={t("create.diskCard")} className="anchor-card">
      {body}
    </Card>
  );
}
