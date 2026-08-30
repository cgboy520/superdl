/** 小件:状态徽标 / 档位标 / 复制按钮。
 *  hex 色 Tag 一律走包内 HexTag(暗色主题下 antd 会把非 preset 色调亮,压成深底白字)。 */

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
  // 展示档位是 tier × pool 的合并键:「共享」落 mig 池是标准档、落 hami 池是经济档
  const variant = skuVariant(tier, pool);
  const meta = metaOf(skuTierMap, variant);
  if (!meta) return <Tag>{variant}</Tag>;
  const tag = <HexTag color={meta.color}>{t(meta.labelKey)}</HexTag>;
  // hint(如「性能可能波动」)收进 Tooltip:内联拼进 Tag 不换行,会把表格规格列压爆
  return "hintKey" in meta && meta.hintKey ? <Tooltip title={t(meta.hintKey)}>{tag}</Tooltip> : tag;
}

/** 实例形态标记。只有服务型实例出标记,开发机是默认形态。 */
export function WorkloadTag({ workloadType }: { workloadType: string }) {
  const { t } = useTranslation(["web", "shared"]);
  if (workloadType !== "service") return null;
  return <Tag color={workloadTypeMap.service.color}>{t(workloadTypeMap.service.labelKey)}</Tag>;
}

/** 包周期标记:「包月 · 剩 23 天」。按量/竞价实例不出标记;已到期转橙并改显订阅状态。 */
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

/** 实例事件 `reason` → 文案。列表失败原因与详情页时间线共用一份映射,表里没有的原样渲染。 */
export function useEventReasonText() {
  const { t } = useTranslation(["web", "shared"]);
  return (reason: string) => {
    const meta = metaOf(instanceEventReasonMap, reason);
    return meta ? t(meta.labelKey) : reason;
  };
}

/** 竞价标记。只有竞价实例出标记,tooltip 里写清回收顺序与提前通知。 */
export function SpotTag({ market }: { market: string }) {
  const { t } = useTranslation(["web", "shared"]);
  if (market !== "spot") return null;
  return (
    <Tooltip title={t(marketMap.spot.hintKey)}>
      <HexTag color={marketMap.spot.color}>{t(marketMap.spot.labelKey)}</HexTag>
    </Tooltip>
  );
}

/** 「可回收」行内标记(列表计费列):与 SpotTag 同色。 */
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
