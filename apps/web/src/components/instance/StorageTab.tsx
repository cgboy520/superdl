/** Instance storage: what the instance mounts — its system disk at /root (usage measured on the Ceph cluster), the data disks snapshotted at its last start (disks added or moved since mount at the next start) and the tenant's storage under /mnt/org. */

import type { InstanceOut } from "@superdl/api-client";
import { fontSize, formatSizeGb, space } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Card, Space, Table, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useDisks, useMyOrg, useOrgMounts } from "../../api/queries";
import { UsageCell } from "../storage/UsageCell";

interface Row {
  key: string;
  path: string;
  what: string;
  size: number | null;
  used: number | null | undefined;
  note: string | null;
}

export function StorageTab({ instance }: { instance: InstanceOut }) {
  const { t } = useTranslation();
  const disksQ = useDisks();
  const { data: org } = useMyOrg();
  const { data: mounts } = useOrgMounts({ enabled: Boolean(org?.org) });
  const snapshot = new Set(instance.data_disk_ids ?? []);
  const disks = (disksQ.data ?? []).filter((d) => d.status !== "deleted" && d.status !== "deleting");
  const systemGb = Number(instance.spec.disk_gb ?? 0);
  const rows: Row[] = [
    {
      key: "system",
      path: "/root",
      what: t("instances.storage.systemDisk"),
      size: systemGb || null,
      used: instance.system_disk_bytes_used,
      note: t("instances.storage.systemNote"),
    },
    ...disks.map((d) => ({
      key: d.uuid,
      path: d.mount_path,
      what: t("instances.storage.dataDisk", { name: d.name }),
      size: d.size_gb,
      used: d.bytes_used,
      note: !snapshot.has(d.id)
        ? t("instances.storage.nextStart")
        : d.status === "grace"
          ? t("instances.storage.readOnlyGrace")
          : d.status === "frozen"
            ? t("instances.storage.frozen")
            : null,
    })),
    ...(mounts ?? [])
      .filter((m) => m.enabled && m.status === "ready")
      .map((m) => ({
        key: m.uuid,
        path: m.mount_path,
        what: t("instances.storage.orgMount"),
        size: null,
        used: undefined,
        note: m.read_only ? t("instances.storage.readOnly") : null,
      })),
  ];
  return (
    <Card size="small">
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Table<Row>
          rowKey="key"
          size="small"
          pagination={false}
          loading={disksQ.isLoading}
          scroll={{ x: 720 }}
          dataSource={rows}
          columns={[
            {
              title: t("instances.storage.colPath"),
              render: (_, r) => <Typography.Text code>{r.path}</Typography.Text>,
            },
            { title: t("instances.storage.colWhat"), render: (_, r) => r.what },
            {
              title: t("instances.storage.colUsage"),
              render: (_, r) => (r.size == null ? "—" : <UsageCell usedBytes={r.used} sizeGb={r.size} />),
            },
            {
              title: t("instances.storage.colNote"),
              render: (_, r) => (r.note ? <Tag variant="filled">{r.note}</Tag> : null),
            },
          ]}
        />
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("instances.storage.footer", { size: formatSizeGb(systemGb) })}{" "}
          <Link to="/storage">{t("instances.storage.manage")}</Link>
        </Typography.Text>
      </Space>
    </Card>
  );
}
