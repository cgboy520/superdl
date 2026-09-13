/** 小件:状态徽标(StatusTag 的 ≤5 行包装)/ 档位标 / 购买模式标 / 复制按钮。 */

import { CheckOutlined, CopyOutlined } from "@ant-design/icons";
import type { InstanceSubscriptionOut } from "@superdl/api-client";
import {
  instanceEventReasonMap,
  instanceStatusMap,
  isSubscriptionExpired,
  marketLabelKey,
  marketMap,
  metaOf,
  serviceStatusMap,
  skuTierMap,
  skuVariant,
  spotReclaimTag,
  statusColors,
  subscriptionStatusMap,
  useThemeColors,
} from "@superdl/ui";
import { HexTag, StatusTag } from "@superdl/ui/components";
import { App, Button, Tag, Tooltip } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";

/** 冻结态附回收倒计时 */
function FrozenCountdown({ status, deadline }: { status: string; deadline?: string | null }) {
  const { formatReclaimCountdown } = useFormat();
  if (status !== "frozen" || !deadline) return null;
  return <Tag color="red">{formatReclaimCountdown(deadline)}</Tag>;
}

export function InstanceStatusBadge({ status, frozenDeadline }: { status: string; frozenDeadline?: string | null }) {
  return (
    <StatusTag
      map={instanceStatusMap}
      value={status}
      variant="badge"
      extra={<FrozenCountdown status={status} deadline={frozenDeadline} />}
    />
  );
}

/** 在线服务的派生状态徽标;unready 的解释 tooltip 由表里的 hintKey 自动出。 */
export function ServiceStatusBadge({ status, frozenDeadline }: { status: string; frozenDeadline?: string | null }) {
  return (
    <StatusTag
      map={serviceStatusMap}
      value={status}
      variant="badge"
      extra={<FrozenCountdown status={status} deadline={frozenDeadline} />}
    />
  );
}

export function TierTag({ tier, pool }: { tier: string; pool?: string | null }) {
  return <StatusTag map={skuTierMap} value={skuVariant(tier, pool)} />;
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
  const colors = useThemeColors();
  if (market !== "subscription" || !subscription) return null;
  const labelKey = marketLabelKey(market, subscription.period);
  const periodLabel = labelKey ? t(labelKey) : subscription.period;
  const expired = isSubscriptionExpired(market, subscription);
  const statusMeta = metaOf(subscriptionStatusMap, subscription.status);
  const tail = expired
    ? statusMeta
      ? t(statusMeta.labelKey)
      : subscription.status
    : formatExpiry(subscription.expires_at);
  return (
    <HexTag color={expired ? statusColors.orange : colors.primary}>
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
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          message.success(t("common.copied"));
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {label}
    </Button>
  );
}
