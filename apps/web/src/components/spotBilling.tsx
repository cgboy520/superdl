/** 竞价(spot)共用件:策略读取、折后时价、折扣角标;市场页 / 创建页 / 实例列表与详情共用。知情同意在 ConsentGate。折扣与宽限窗一律从 `/policies` 读。 */

import { fontSize, mulPrice, spotHourlyPrice } from "@superdl/ui";
import { Space, Typography } from "antd";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { usePolicies } from "../api/queries";
import { useFormat } from "@superdl/ui";

export interface SpotPolicy {
  /** 竞价价占按量价的百分数(40 = 4 折) */
  discountPct: number;
  /** 抢占通知到真删 Pod 的宽限窗(秒) */
  graceSeconds: number;
}

/** 竞价策略;policies 未就绪返回 undefined,调用方灰置竞价档。 */
export function useSpotPolicy(): SpotPolicy | undefined {
  const { data: policies } = usePolicies();
  return useMemo(
    () =>
      policies ? { discountPct: policies.spot_discount_pct, graceSeconds: policies.spot_grace_seconds } : undefined,
    [policies],
  );
}

/** 竞价时价(单份);策略未就绪返回 undefined。 */
export function spotPriceOf(baseHourly: string, policy: SpotPolicy | undefined): string | undefined {
  return policy ? spotHourlyPrice(baseHourly, policy.discountPct) : undefined;
}

/** 折后时价 + 原价划线。`units` 是「一小时收几份」(GPU 卡数;CPU 恒 1)。 */
export function SpotPriceInline({
  baseHourly,
  units,
  policy,
}: {
  baseHourly: string;
  units: number;
  policy: SpotPolicy | undefined;
}) {
  const { formatHourlyPrice } = useFormat();
  const spot = spotPriceOf(baseHourly, policy);
  if (!spot) return <>{formatHourlyPrice(mulPrice(baseHourly, units))}</>;
  return (
    <Space size={8} align="baseline">
      <Typography.Text type="secondary" delete style={{ fontSize: fontSize.body }}>
        {formatHourlyPrice(mulPrice(baseHourly, units))}
      </Typography.Text>
      <span>{formatHourlyPrice(mulPrice(spot, units))}</span>
    </Space>
  );
}

export function SpotOffLabel({ policy }: { policy: SpotPolicy | undefined }) {
  const { t } = useTranslation();
  const { formatSpotDiscount } = useFormat();
  if (!policy) return null;
  return (
    <Typography.Text type="secondary" style={{ marginInlineStart: 4 }}>
      {t("market.spotOff", { discount: formatSpotDiscount(policy.discountPct) })}
    </Typography.Text>
  );
}
