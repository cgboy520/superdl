/** 小件:状态徽标 / 档位标 / 金额文本 / 复制按钮。 */

import { CheckOutlined, CopyOutlined } from "@ant-design/icons";
import type { DiskStatus, InstanceStatus } from "@superdl/ui";
import {
  diskStatusMap,
  formatCountdown,
  formatHourlyPrice,
  formatMoney,
  instanceStatusMap,
  skuTierMap,
  tabularNums,
  type SkuTier,
} from "@superdl/ui";
import { App, Badge, Button, Tag } from "antd";
import { useState } from "react";

export function InstanceStatusBadge({
  status,
  frozenDeadline,
}: {
  status: string;
  frozenDeadline?: string | null;
}) {
  const meta = instanceStatusMap[status as InstanceStatus] ?? {
    label: status,
    badge: "default" as const,
    color: "#999",
  };
  return (
    <span>
      <Badge status={meta.badge} text={meta.label} />
      {status === "frozen" && frozenDeadline && (
        <Tag color="red" style={{ marginLeft: 8 }}>
          {formatCountdown(frozenDeadline)}后回收
        </Tag>
      )}
    </span>
  );
}

export function DiskStatusBadge({ status }: { status: string }) {
  const meta = diskStatusMap[status as DiskStatus] ?? {
    label: status,
    badge: "default" as const,
    color: "#999",
  };
  return <Badge status={meta.badge} text={meta.label} />;
}

export function TierTag({ tier }: { tier: string }) {
  const meta = skuTierMap[tier as SkuTier];
  if (!meta) return <Tag>{tier}</Tag>;
  return (
    <Tag color={meta.color}>
      {meta.label}
      {meta.hint ? `(${meta.hint})` : ""}
    </Tag>
  );
}

export function Money({ value, suffix }: { value: string | null | undefined; suffix?: string }) {
  return (
    <span style={tabularNums}>
      {formatMoney(value)}
      {suffix}
    </span>
  );
}

export function HourlyPrice({ value }: { value: string | null | undefined }) {
  return <span style={tabularNums}>{formatHourlyPrice(value)}</span>;
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
