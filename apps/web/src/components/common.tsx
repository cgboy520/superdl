/** 小件:状态徽标 / 档位标 / 复制按钮。 */

import { CheckOutlined, CopyOutlined } from "@ant-design/icons";
import { diskStatusMap, formatCountdown, instanceStatusMap, metaOf, skuTierMap } from "@superdl/ui";
import { App, Badge, Button, Tag } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export function InstanceStatusBadge({
  status,
  frozenDeadline,
}: {
  status: string;
  frozenDeadline?: string | null;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const meta = metaOf(instanceStatusMap, status);
  return (
    <span>
      <Badge status={meta?.badge ?? "default"} text={meta ? t(meta.labelKey) : status} />
      {status === "frozen" && frozenDeadline && (
        <Tag color="red" style={{ marginLeft: 8 }}>
          {formatCountdown(frozenDeadline)}后回收
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
  return (
    <Tag color={meta.color}>
      {t(meta.labelKey)}
      {"hintKey" in meta ? `(${t(meta.hintKey)})` : ""}
    </Tag>
  );
}

export function CopyButton({ text, label }: { text: string; label?: string }) {
  const { message } = App.useApp();
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="small"
      icon={copied ? <CheckOutlined /> : <CopyOutlined />}
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        message.success("已复制");
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {label}
    </Button>
  );
}
