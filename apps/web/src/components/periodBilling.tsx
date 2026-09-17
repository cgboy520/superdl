/** Subscription (prepaid) shared pieces: discount reading, local quote, units and the three-line cost breakdown; shared by the market / create pages and the renewal modal, same source as the backend core/pricing.py. The local quote is a preview only, the charged amount follows the API quote, the breakdown must carry a hint. */

import { fontSize, periodMap, quoteSubscription, space, type BillingPeriod, type PeriodQuote } from "@superdl/ui";
import { Space, Typography } from "antd";
import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { usePolicies } from "../api/queries";
import { useFormat } from "@superdl/ui";

/** Discount percentages of the four periods (80 = 20 % off); undefined while policies are pending. */
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

/** Discount strength (percent, the 20 in -20%). */
export function discountOff(pct: number): number {
  return 100 - pct;
}

/** Local quote: no quote while the discounts are pending. */
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

/** Three-line cost breakdown: instance fee (list price) → period discount → payable. */
export function PeriodQuoteRows({
  quote,
  gpuCount,
  cpu,
  hint,
}: {
  quote: PeriodQuote;
  /** GPU card count; CPU specs pass 0 and set cpu */
  gpuCount: number;
  cpu?: boolean;
  /** Basis hint ("the final quote on the create page prevails"), given by the caller per scene */
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
