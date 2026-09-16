/** Spot shared pieces: policy reading, discounted hourly price, discount badge; shared by the market / create pages and the instance list / detail. Consent lives in ConsentGate. Discount and grace window are always read from `/policies`. */

import { fontSize, mulPrice, space, spotHourlyPrice } from "@superdl/ui";
import { Space, Typography } from "antd";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { usePolicies } from "../api/queries";
import { useFormat } from "@superdl/ui";

export interface SpotPolicy {
  /** Spot price as a percentage of the on-demand price (40 = 60 % off) */
  discountPct: number;
  /** Grace window (seconds) from the preemption notice to the real Pod deletion */
  graceSeconds: number;
}

/** Spot policy; undefined while policies are pending, the caller greys the spot tier. */
export function useSpotPolicy(): SpotPolicy | undefined {
  const { data: policies } = usePolicies();
  return useMemo(
    () =>
      policies ? { discountPct: policies.spot_discount_pct, graceSeconds: policies.spot_grace_seconds } : undefined,
    [policies],
  );
}

/** Spot hourly price (one unit); undefined while the policy is pending. */
export function spotPriceOf(baseHourly: string, policy: SpotPolicy | undefined): string | undefined {
  return policy ? spotHourlyPrice(baseHourly, policy.discountPct) : undefined;
}

/** Discounted hourly price + struck-through list price. `units` is "units per hour" (GPU card count; CPU always 1). */
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
    <Space size={space.sm} align="baseline">
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
