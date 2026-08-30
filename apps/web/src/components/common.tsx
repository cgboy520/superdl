/** 小件:状态徽标 / 档位标 / 复制按钮。 */

import { CheckOutlined, CopyOutlined } from "@ant-design/icons";
import type { InstanceSubscriptionOut } from "@superdl/api-client";
import {
  colorPrimary,
  diskStatusMap,
  instanceEventReasonMap,
  instanceStatusMap,
  isSubscriptionExpired,
  marketLabelKey,
  marketMap,
  metaOf,
  skuTierMap,
  skuVariant,
  spotReclaimTag,
  statusColors,
  subscriptionStatusMap,
  workloadTypeMap,
} from "@superdl/ui";
import { HexTag } from "@superdl/ui/components";
import { App, Badge, Button, Tag, Tooltip } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";

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
        <Tag color="red" style={{ marginInlineStart: 8 }}>
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

export function TierTag({ tier, pool }: { tier: string; pool?: string | null }) {
  const { t } = useTranslation(["web", "shared"]);
  const variant = skuVariant(tier, pool);
  const meta = metaOf(skuTierMap, variant);
  if (!meta) return <Tag>{variant}</Tag>;
  const tag = <HexTag color={meta.color}>{t(meta.labelKey)}</HexTag>;
  return "hintKey" in meta && meta.hintKey ? <Tooltip title={t(meta.hintKey)}>{tag}</Tooltip> : tag;
}

export function WorkloadTag({ workloadType }: { workloadType: string }) {
  const { t } = useTranslation(["web", "shared"]);
  if (workloadType !== "service") return null;
  return <Tag color={workloadTypeMap.service.color}>{t(workloadTypeMap.service.labelKey)}</Tag>;
}

export function SubscriptionTag({
  market,
  subscription,
}: {
  market: string;
  subscription: InstanceSubscriptionOut | null | undefined;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatExpiry } = useFormat();
  if (market !== "subscription" || !subscription) return null;
  const labelKey = marketLabelKey(market, subscription.period);
  const periodLabel = labelKey ? t(labelKey) : subscription.period;
  const expired = isSubscriptionExpired(market, subscription);
  const statusMeta = metaOf(subscriptionStatusMap, subscription.status);
  const tail = expired
    ? (statusMeta ? t(statusMeta.labelKey) : subscription.status)
    : formatExpiry(subscription.expires_at);
  return (
    <HexTag color={expired ? statusColors.orange : colorPrimary}>
      {tail ? t("period.tagWithExpiry", { period: periodLabel, expiry: tail }) : periodLabel}
    </HexTag>
  );
}

export function useEventReasonText() {
  const { t } = useTranslation(["web", "shared"]);
  return (reason: string) => {
    const meta = metaOf(instanceEventReasonMap, reason);
    return meta ? t(meta.labelKey) : reason;
  };
}

export function SpotTag({ market }: { market: string }) {
  const { t } = useTranslation(["web", "shared"]);
  if (market !== "spot") return null;
  return (
    <Tooltip title={t(marketMap.spot.hintKey)}>
      <HexTag color={marketMap.spot.color}>{t(marketMap.spot.labelKey)}</HexTag>
    </Tooltip>
  );
}

export function SpotReclaimTag({ market }: { market: string }) {
  const { t } = useTranslation(["web", "shared"]);
  if (market !== "spot") return null;
  return (
    <Tooltip title={t(spotReclaimTag.hintKey)}>
      <HexTag color={spotReclaimTag.color}>{t(spotReclaimTag.labelKey)}</HexTag>
    </Tooltip>
  );
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
