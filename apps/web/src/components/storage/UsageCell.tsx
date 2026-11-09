/** Used space of a disk as last measured by the storage patrol (Ceph subvolume usage, refreshed every few minutes), with a bar against its size. */

import { fontSize, formatBytes, formatDateTime, formatSizeGb } from "@superdl/ui";
import { Progress, Space, Tooltip, Typography } from "antd";
import { useTranslation } from "react-i18next";

const GIB = 1024 ** 3;

export function UsageCell({
  usedBytes,
  sizeGb,
  checkedAt,
}: {
  usedBytes: number | null | undefined;
  sizeGb: number;
  checkedAt?: string | null;
}) {
  const { t } = useTranslation();
  if (usedBytes == null) {
    return <Typography.Text type="secondary">{t("storage.usageUnknown")}</Typography.Text>;
  }
  const pct = Math.min(100, Math.round((usedBytes / (sizeGb * GIB)) * 100));
  return (
    <Tooltip title={checkedAt ? t("storage.usageCheckedAt", { time: formatDateTime(checkedAt) }) : undefined}>
      <Space orientation="vertical" size={0} style={{ minWidth: 140 }}>
        <Typography.Text style={{ fontSize: fontSize.caption }}>
          {t("storage.usageLine", { used: formatBytes(usedBytes), size: formatSizeGb(sizeGb) })}
        </Typography.Text>
        <Progress
          percent={pct}
          size="small"
          showInfo={false}
          status={pct >= 90 ? "exception" : "normal"}
          aria-label={t("storage.colUsage")}
        />
      </Space>
    </Tooltip>
  );
}
