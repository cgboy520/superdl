/** 包周期(预付)共用件:折扣读取、本地报价、量词与费用明细三行;市场页 / 创建页 / 续费 modal 共用,与后端 core/pricing.py 同源。本地报价只作预览,成交金额以接口 quote 为准,明细区须挂 hint。 */

import { fontSize, periodMap, quoteSubscription, space, type BillingPeriod, type PeriodQuote } from "@superdl/ui";
import { Space, Typography } from "antd";
import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { usePolicies } from "../api/queries";
import { useFormat } from "@superdl/ui";

/** 四个周期的折扣百分数(80 = 8 折);policies 未就绪返回 undefined。 */
export function usePeriodDiscounts(): Record<BillingPeriod, number> | undefined {
  const { data: policies } = usePolicies();
  return useMemo(
    () =>
      policies
        ? {
            day: policies.period_discount_day,
            week: policies.period_discount_week,
            month: policies.period_discount_month,
            year: policies.period_discount_year,
          }
        : undefined,
    [policies],
  );
}

/** 折扣力度(百分数,-20% 里的 20)。 */
export function discountOff(pct: number): number {
  return 100 - pct;
}

/** 本地报价:折扣未就绪不报价。 */
export function periodQuoteOf(
  baseHourly: string,
  opts: { units: number; period: BillingPeriod; periodCount: number },
  discounts: Record<BillingPeriod, number> | undefined,
): PeriodQuote | undefined {
  if (!discounts) return undefined;
  return quoteSubscription(baseHourly, { ...opts, discountPct: discounts[opts.period] });
}

export function PeriodCountUnit({ period }: { period: BillingPeriod }) {
  const { t } = useTranslation();
  switch (period) {
    case "day":
      return <>{t("period.unitDay")}</>;
    case "week":
      return <>{t("period.unitWeek")}</>;
    case "year":
      return <>{t("period.unitYear")}</>;
    default:
      return <>{t("period.unitMonth")}</>;
  }
}

function QuoteRow({ label, value, strong }: { label: ReactNode; value: string; strong?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 24 }}>
      <span>{label}</span>
      <Typography.Text strong={strong} style={{ whiteSpace: "nowrap" }}>
        {value}
      </Typography.Text>
    </div>
  );
}

/** 费用明细三行:实例费用(原价)→ 周期优惠 → 应付。 */
export function PeriodQuoteRows({
  quote,
  gpuCount,
  cpu,
  hint,
}: {
  quote: PeriodQuote;
  /** GPU 卡数;CPU 规格传 0 并置 cpu */
  gpuCount: number;
  cpu?: boolean;
  /** 口径提示(「以创建页最终报价为准」),由调用方按场景给 */
  hint?: string;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatHourlyPrice, formatMoney } = useFormat();
  const periodLabel = t(periodMap[quote.period].labelKey);
  return (
    <Space orientation="vertical" size={space.xs} style={{ width: "100%", minWidth: 300 }}>
      <QuoteRow
        label={
          cpu
            ? t("period.basisWhole", {
                unit: formatHourlyPrice(quote.baseHourly),
                hours: quote.hours,
              })
            : t("period.basisCards", {
                unit: formatHourlyPrice(quote.baseHourly),
                count: gpuCount,
                hours: quote.hours,
              })
        }
        value={formatMoney(quote.listAmount)}
      />
      <QuoteRow
        label={t("period.rowDiscount", {
          period: periodLabel,
          off: discountOff(quote.discountPct),
        })}
        value={`-${formatMoney(quote.discountAmount)}`}
      />
      <QuoteRow label={t("period.rowPayable")} value={formatMoney(quote.amount)} strong />
      {hint && (
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {hint}
        </Typography.Text>
      )}
    </Space>
  );
}
