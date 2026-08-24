/** 小件:状态徽标 / 档位标 / 复制按钮。 */

import { CheckOutlined, CopyOutlined } from "@ant-design/icons";
import { diskStatusMap, instanceStatusMap, metaOf, skuTierMap } from "@superdl/ui";
import { App, Badge, Button, Tag, Tooltip } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "../lib/format";

export function InstanceStatusBadge({
  status,
  frozenDeadline,
}: {
  status: string;
  frozenDeadline?: string | null;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatReclaimCountdown } = useFormat();
  const meta = metaOf(instanceStatusMap, status);
  return (
    <span>
      <Badge status={meta?.badge ?? "default"} text={meta ? t(meta.labelKey) : status} />
      {status === "frozen" && frozenDeadline && (
        <Tag color="red" style={{ marginLeft: 8 }}>
          {formatReclaimCountdown(frozenDeadline)}
        </Tag>
      )}
    </span>
  );
}

export function DiskStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation(["web", "shared"]);
  const meta = metaOf(diskStatusMap, status);
  return <Badge status={meta?.badge ?? "default"} text={meta ? t(meta.labelKey) : status} />;
}

export function TierTag({ tier }: { tier: string }) {
  const { t } = useTranslation(["web", "shared"]);
  const meta = metaOf(skuTierMap, tier);
  if (!meta) return <Tag>{tier}</Tag>;
  const tag = <Tag color={meta.color}>{t(meta.labelKey)}</Tag>;
  // hint(如「性能可能波动」)收进 Tooltip:内联拼进 Tag 不换行,会把表格规格列压爆
  return "hintKey" in meta && meta.hintKey ? <Tooltip title={t(meta.hintKey)}>{tag}</Tooltip> : tag;
}

export function CopyButton({ text, label }: { text: string; label?: string }) {
  const { message } = App.useApp();
  const { t } = useTranslation(["web", "shared"]);
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="small"
      icon={copied ? <CheckOutlined /> : <CopyOutlined />}
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        message.success(t("common.copied"));
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {label}
    </Button>
  );
}
