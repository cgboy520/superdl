/** Billing column (shared by the instance and service lists): subscription = tier tag + period price; on-demand / spot = marker + hourly price + today's spend. Services pass current_instance ?? rollout_instance; without an instance "—" is shown. Renewal lives under "More". */

import type { InstanceOut } from "@superdl/api-client";
import { fontSize, space } from "@superdl/ui";
import { useFormat } from "@superdl/ui";
import { moneyOr } from "@superdl/ui/components";
import { Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { SpotReclaimTag, SpotTag, SubscriptionTag } from "./common";

export function BillingCell({
  instance,
  todayByInstance,
  dailyReady,
}: {
  instance: InstanceOut | null;
  /** instance_id → amount billed today (decimal string) */
  todayByInstance: ReadonlyMap<number, string>;
  /** Whether the daily consumption query is ready ("—" on failure) */
  dailyReady: boolean;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatHourlyPrice, formatMoney, formatPeriodPrice } = useFormat();
  if (!instance) return <Typography.Text type="secondary">—</Typography.Text>;
  const r = instance;
  return r.market === "subscription" && r.subscription ? (
    <Space orientation="vertical" size={0} align="start">
      <SubscriptionTag market={r.market} subscription={r.subscription} />
      <span>{formatPeriodPrice(r.subscription.amount_paid, r.subscription.period, r.subscription.period_count)}</span>
    </Space>
  ) : (
    <Space orientation="vertical" size={0}>
      <Space size={space.sm}>
        {r.market === "spot" ? (
          <SpotTag market={r.market} />
        ) : (
          <Tag style={{ marginInlineEnd: 0 }}>{t("instances.payAsYouGo")}</Tag>
        )}
        <span>{t("instances.pricePerCard", { price: formatHourlyPrice(r.price_hourly), count: r.gpu_count })}</span>
      </Space>
      <Space size={space.sm}>
        <SpotReclaimTag market={r.market} />
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("instances.todayCost", { amount: moneyOr(formatMoney(todayByInstance.get(r.id) ?? "0.00"), dailyReady) })}
        </Typography.Text>
      </Space>
    </Space>
  );
}
