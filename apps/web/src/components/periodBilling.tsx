/**
 * 包周期(预付)共用件:折扣读取、本地报价、量词与费用明细三行。
 * 市场页、创建页、续费 modal 三处共用 —— 折扣与口径各写一份,迟早出现
 * 「页面显示 8 折、实际扣 8.5 折」这类没人能复现的差异(与后端 core/pricing.py 同一条纪律)。
 *
 * **本地报价只用于下单前的预览**:成交金额一律以接口返回的 quote 为准,
 * 明细区必须挂 hint 说清楚这一点。
 */

import {
  periodMap,
  quoteSubscription,
  type BillingPeriod,
  type PeriodQuote,
} from "@superdl/ui";
import { Space, Typography } from "antd";
import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { usePolicies } from "../api/queries";
import { useFormat } from "../lib/format";

/** 四个周期的折扣百分数(80 = 8 折);policies 未就绪时返回 undefined,调用方不出报价。 */
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

/** 本地报价:折扣未就绪就不报价(宁可不显示,也不按硬编码的折扣算一个假数)。 */
export function periodQuoteOf(
  baseHourly: string | null | undefined,
  opts: { units: number; period: BillingPeriod; periodCount: number },
  discounts: Record<BillingPeriod, number> | undefined,
): PeriodQuote | undefined {
  if (!discounts) return undefined;
  return quoteSubscription(baseHourly, { ...opts, discountPct: discounts[opts.period] });
}

/**
 * 周期量词(「3 个月」里的「个月」)。四个分支写死而不是拼 key ——
 * i18next-cli 的 extract 看不见动态键,会把它们当未引用删掉。
 */
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

/** 明细一行:左标签 + 右金额,金额右对齐(三行的小数点要对齐才读得出加减关系)。 */
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

/**
 * 费用明细三行:实例费用(原价)→ 周期优惠 → 应付。
 * 三个数由 quoteSubscription 一次算出并自洽(优惠 = 原价 − 应付),这里只渲染,不做乘法。
 */
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
  /** 「以创建页最终报价为准」这类口径提示,由调用方按场景给 */
  hint?: string;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatHourlyPrice, formatMoney } = useFormat();
  const periodLabel = t(periodMap[quote.period].labelKey);
  return (
    <Space orientation="vertical" size={4} style={{ width: "100%", minWidth: 300 }}>
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
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {hint}
        </Typography.Text>
      )}
    </Space>
  );
}
