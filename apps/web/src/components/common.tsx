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

export function TierTag({ tier, pool }: { tier: string; pool?: string | null }) {
  const { t } = useTranslation(["web", "shared"]);
  // 展示档位是 tier × pool 的合并键:「共享」落 mig 池是标准档、落 hami 池是经济档
  const variant = skuVariant(tier, pool);
  const meta = metaOf(skuTierMap, variant);
  if (!meta) return <Tag>{variant}</Tag>;
  const tag = <Tag color={meta.color}>{t(meta.labelKey)}</Tag>;
  // hint(如「性能可能波动」)收进 Tooltip:内联拼进 Tag 不换行,会把表格规格列压爆
  return "hintKey" in meta && meta.hintKey ? <Tooltip title={t(meta.hintKey)}>{tag}</Tooltip> : tag;
}

/**
 * 实例形态标记。只有服务型实例出标记 —— 开发机是默认形态,给每一行挂一个「开发机」
 * 徽标只是噪声。
 */
export function WorkloadTag({ workloadType }: { workloadType: string }) {
  const { t } = useTranslation(["web", "shared"]);
  if (workloadType !== "service") return null;
  const meta = metaOf(workloadTypeMap, workloadType);
  if (!meta) return null;
  return <Tag color={meta.color}>{t(meta.labelKey)}</Tag>;
}

/**
 * 包周期标记:「包月 · 剩 23 天」。按量/竞价实例不出标记 —— 按量是默认买法,
 * 给每一行挂一个「按量」徽标只是噪声(与 WorkloadTag 同一条口径)。
 * 已到期转橙并改显订阅状态:剩余天数对一台已经停掉的机器没有意义。
 */
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
    <Tag color={expired ? statusColors.orange : colorPrimary}>
      {tail ? t("period.tagWithExpiry", { period: periodLabel, expiry: tail }) : periodLabel}
    </Tag>
  );
}

/**
 * 实例事件 `reason` → 文案。列表的失败原因与详情页时间线共用一份映射;
 * 表里没有的(后端调度器回填的自由文本)原样渲染 —— 没配文案不是吞掉整句的理由。
 */
export function useEventReasonText() {
  const { t } = useTranslation(["web", "shared"]);
  return (reason: string) => {
    const meta = metaOf(instanceEventReasonMap, reason);
    return meta ? t(meta.labelKey) : reason;
  };
}

/**
 * 竞价标记。只有竞价实例出标记 —— 按量是默认买法(与 WorkloadTag / SubscriptionTag 同口径)。
 * 「可被回收」是这台机器与别人最要紧的区别,tooltip 里写清回收顺序与提前通知。
 */
export function SpotTag({ market }: { market: string }) {
  const { t } = useTranslation(["web", "shared"]);
  if (market !== "spot") return null;
  return (
    <Tooltip title={t(marketMap.spot.hintKey)}>
      <Tag color={marketMap.spot.color} style={{ marginInlineEnd: 0 }}>
        {t(marketMap.spot.labelKey)}
      </Tag>
    </Tooltip>
  );
}

/** 「可回收」行内标记(列表计费列):与 SpotTag 同色,说的是同一件事的两面。 */
export function SpotReclaimTag({ market }: { market: string }) {
  const { t } = useTranslation(["web", "shared"]);
  if (market !== "spot") return null;
  return (
    <Tooltip title={t(spotReclaimTag.hintKey)}>
      <Tag color={spotReclaimTag.color} style={{ marginInlineEnd: 0 }}>
        {t(spotReclaimTag.labelKey)}
      </Tag>
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
