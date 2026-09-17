/** Money, duration and countdown formatting. Amounts are decimal strings; currency text comes from
 *  `Intl.NumberFormat` for the deployment currency (see hooks/useCurrency); `t` is passed in by the caller. */

import { isBillingPeriod, type BillingPeriod } from "./status";

/** Shared copy keys used by the format functions. */
type SharedFormatKey =
  | "shared:format.perHour"
  | "shared:format.perPeriod"
  | "shared:format.perPeriodCount"
  | `shared:format.periodUnit.${BillingPeriod}`
  | "shared:format.duration.zero"
  | "shared:format.duration.lessThanMinute"
  | "shared:format.duration.h"
  | "shared:format.duration.m"
  | "shared:format.duration.hm"
  | "shared:format.countdown.expired"
  | "shared:format.countdown.hours"
  | "shared:format.countdown.minutes"
  | "shared:format.countdown.reclaimHours"
  | "shared:format.countdown.reclaimMinutes"
  | "shared:format.countdown.reclaimNow"
  | "shared:format.daysLeft.expired"
  | "shared:format.daysLeft.dueToday"
  | "shared:format.daysLeft.count"
  | "shared:format.spotDiscount";

export type SharedT = (key: SharedFormatKey, opts?: Record<string, unknown>) => string;

/** Intl formatter cache keyed by locale, currency and fraction bounds; `null` currency = plain number. */
const formatterCache = new Map<string, Intl.NumberFormat | null>();

/** `null` when Intl rejects the currency code (unknown ISO code). */
function buildNumberFormat(
  locale: string,
  currency: string | null,
  minFraction: number,
  maxFraction: number,
): Intl.NumberFormat | null {
  try {
    return new Intl.NumberFormat(locale, {
      ...(currency ? { style: "currency", currency } : {}),
      minimumFractionDigits: minFraction,
      maximumFractionDigits: maxFraction,
    });
  } catch {
    return null;
  }
}

function numberFormat(
  locale: string,
  currency: string | null,
  minFraction: number,
  maxFraction: number,
): Intl.NumberFormat | null {
  const key = `${locale}|${currency ?? ""}|${minFraction}|${maxFraction}`;
  const hit = formatterCache.get(key);
  if (hit !== undefined) return hit;
  const fmt = buildNumberFormat(locale, currency, minFraction, maxFraction);
  formatterCache.set(key, fmt);
  return fmt;
}

/** Minor-unit digits of an ISO 4217 code per Intl (JPY → 0, USD → 2); unknown or null → 2. */
export function minorUnitsOf(currency: string | null): number {
  if (!currency) return 2;
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).resolvedOptions().maximumFractionDigits ?? 2;
  } catch {
    return 2;
  }
}

/** Currency symbol Intl uses for the locale ("¥" for CNY in zh-CN, "CN¥" in en-US, "$" for USD in en-US);
 *  unknown or null currency → "". */
export function currencySymbol(locale: string, currency: string | null): string {
  const fmt = currency ? numberFormat(locale, currency, 0, 0) : null;
  if (!fmt) return "";
  return fmt.formatToParts(0).find((p) => p.type === "currency")?.value ?? "";
}

/** Decimal string cut (never rounded) to `digits` fraction digits; the display layer does no arithmetic. */
function truncateFraction(amount: string, digits: number): string {
  const neg = amount.startsWith("-");
  const [intRaw = "0", fracRaw = ""] = (neg ? amount.slice(1) : amount).split(".");
  const int = intRaw === "" ? "0" : intRaw;
  const frac = fracRaw.slice(0, digits);
  return `${neg ? "-" : ""}${int}${frac ? `.${frac}` : ""}`;
}

/** Plain-number fallback with a fixed fraction range (unknown currency or Intl unavailable). */
function plainNumber(amount: string, locale: string, minFraction: number, maxFraction: number): string {
  const fmt = numberFormat(locale, null, minFraction, maxFraction);
  return fmt ? fmt.format(amount as unknown as number) : amount;
}

/** Amount string → localized currency text, truncated to the currency's minor units
 *  ("1234.5" → "¥1,234.50" zh-CN/CNY, "$1,234.50" en-US/USD, "¥1,235" en-US/JPY from "1235.9" → "1235").
 *  Null or unknown currency → number without a symbol. */
export function formatMoney(amount: string, locale: string, currency: string | null): string {
  const digits = minorUnitsOf(currency);
  const value = truncateFraction(amount, digits);
  const fmt = currency ? numberFormat(locale, currency, digits, digits) : null;
  return fmt ? fmt.format(value as unknown as number) : plainNumber(value, locale, digits, digits);
}

/** Unit price (4 fraction digits max, at least the currency's minor units, trailing zeros dropped):
 *  "0.1250" → "¥0.125", "1.6800" → "¥1.68". */
export function formatPrice(price: string, locale: string, currency: string | null): string {
  const digits = minorUnitsOf(currency);
  const value = truncateFraction(price, 4);
  const fmt = currency ? numberFormat(locale, currency, digits, 4) : null;
  return fmt ? fmt.format(value as unknown as number) : plainNumber(value, locale, digits, 4);
}

/** Hourly price: "1.68" → "¥1.68/时" / "$1.68/hr". */ // cjk-ok
export function formatHourlyPrice(price: string, t: SharedT, locale: string, currency: string | null): string {
  return t("shared:format.perHour", { price: formatPrice(price, locale, currency) });
}

/** Decimal string → BigInt fixed point (signed; empty = 0). digits = fraction digits (amounts 2 / prices 4). */
function scaleAmount(s: string, digits: 2 | 4): bigint {
  if (s === "") return 0n;
  const neg = s.startsWith("-");
  const [int = "0", frac = ""] = (neg ? s.slice(1) : s).split(".");
  const v = BigInt(int + (frac + "0000").slice(0, digits));
  return neg ? -v : v;
}

/** BigInt fixed point → decimal string (signed); no decimal point with digits 0. */
function unscale(scaled: bigint, digits: 0 | 2 | 4): string {
  const neg = scaled < 0n;
  const s = (neg ? -scaled : scaled).toString().padStart(digits + 1, "0");
  if (digits === 0) return `${neg ? "-" : ""}${s}`;
  return `${neg ? "-" : ""}${s.slice(0, -digits)}.${s.slice(-digits)}`;
}

/** Decimal string × integer (BigInt, 4 dp). Display layer only. */
export function mulPrice(price: string, count: number): string {
  return unscale(scaleAmount(price, 4) * BigInt(count), 4);
}

/** Data-disk daily estimate: GB·month price × GB ÷ 30, HALF_EVEN to the currency's minor unit (the caller passes
 *  the active currency's value so the estimate rounds like the settlement). */
export function diskDailyEstimate(priceGbMonth: string, gb: number, minorUnits: 0 | 2): string {
  if (gb <= 0 || !Number.isInteger(gb)) return unscale(0n, minorUnits);
  const monthlyScaled = scaleAmount(priceGbMonth, 4) * BigInt(gb);
  return unscale(halfEvenDiv(monthlyScaled, 30n * 10n ** BigInt(4 - minorUnits)), minorUnits);
}

/** Non-negative integer division with ROUND_HALF_EVEN. */
function halfEvenDiv(numerator: bigint, denominator: bigint): bigint {
  const q = numerator / denominator;
  const twice = (numerator - q * denominator) * 2n;
  if (twice > denominator) return q + 1n;
  if (twice < denominator) return q;
  return q % 2n === 0n ? q : q + 1n;
}

/** Fixed hours per billing period. */
export const PERIOD_HOURS: Record<BillingPeriod, number> = {
  day: 24,
  week: 24 * 7,
  month: 24 * 30,
  year: 24 * 365,
};

/** Cap on periods per order / renewal. */
export const MAX_PERIOD_COUNT = 36;

/** Billing units per hour; a GPU count of 0 bills one whole-machine unit. */
export function billingUnits(gpuCount: number): number {
  return gpuCount || 1;
}

/** Local subscription quote preview, money fields as decimal strings. */
export interface PeriodQuote {
  period: BillingPeriod;
  periodCount: number;
  hours: number;
  discountPct: number;
  baseHourly: string;
  unitPrice: string;
  listAmount: string;
  discountAmount: string;
  amount: string;
}

/** Local quote: the discounted hourly price is rounded HALF_EVEN to 4 dp first, then × units × hours and rounded to the cent. */
export function quoteSubscription(
  baseHourly: string,
  opts: { units: number; period: BillingPeriod; periodCount: number; discountPct: number },
): PeriodQuote {
  const { units, period, periodCount, discountPct } = opts;
  const hours = PERIOD_HOURS[period] * periodCount;
  const base4 = scaleAmount(baseHourly, 4);
  const unit4 = halfEvenDiv(base4 * BigInt(discountPct), 100n);
  const factor = BigInt(units) * BigInt(hours);
  const listCents = halfEvenDiv(base4 * factor, 100n);
  const amountCents = halfEvenDiv(unit4 * factor, 100n);
  return {
    period,
    periodCount,
    hours,
    discountPct,
    baseHourly: unscale(base4, 4),
    unitPrice: unscale(unit4, 4),
    listAmount: unscale(listCents, 2),
    discountAmount: unscale(listCents - amountCents, 2),
    amount: unscale(amountCents, 2),
  };
}

/** On-demand hourly price × discount percent / 100, HALF_EVEN to 4 dp. */
export function spotHourlyPrice(baseHourly: string, discountPct: number): string {
  return unscale(halfEvenDiv(scaleAmount(baseHourly, 4) * BigInt(discountPct), 100n), 4);
}

/** Discount phrase: zh "4 折", en "40% of on-demand"; the caller embeds it as a value in a sentence. */ // cjk-ok
export function formatSpotDiscount(discountPct: number, t: SharedT, locale: string): string {
  if (!locale.startsWith("zh")) return t("shared:format.spotDiscount", { off: discountPct });
  const whole = Math.trunc(discountPct / 10);
  const rest = discountPct % 10;
  return t("shared:format.spotDiscount", { off: rest === 0 ? `${whole}` : `${whole}.${rest}` });
}

/** Seconds → "X hr Y min" (en uses abbreviated units). */
export function formatDuration(seconds: number, t: SharedT): string {
  if (seconds < 60) {
    return seconds <= 0 ? t("shared:format.duration.zero") : t("shared:format.duration.lessThanMinute");
  }
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h === 0) return t("shared:format.duration.m", { m });
  if (m === 0) return t("shared:format.duration.h", { h });
  return t("shared:format.duration.hm", { h, m });
}

/** Deadline → "47h left" / "30m left" / "Expired". */
export function formatCountdown(deadline: string | Date, t: SharedT, now: Date = new Date()): string {
  const end = typeof deadline === "string" ? new Date(deadline) : deadline;
  const ms = end.getTime() - now.getTime();
  if (ms <= 0) return t("shared:format.countdown.expired");
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return t("shared:format.countdown.hours", { count: hours });
  return t("shared:format.countdown.minutes", { count: Math.max(1, Math.floor(ms / 60_000)) });
}

/** Whole inline label of the frozen row: "reclaimed in 47h" (its own key). */
export function formatReclaimCountdown(deadline: string | Date, t: SharedT, now: Date = new Date()): string {
  const end = typeof deadline === "string" ? new Date(deadline) : deadline;
  const ms = end.getTime() - now.getTime();
  if (ms <= 0) return t("shared:format.countdown.reclaimNow");
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return t("shared:format.countdown.reclaimHours", { count: hours });
  return t("shared:format.countdown.reclaimMinutes", { count: Math.max(1, Math.floor(ms / 60_000)) });
}

/** Day-level countdown: deadline → "X days left" / "Due today" / "Expired". */
export function formatDaysUntil(deadline: string | Date, t: SharedT, now: Date = new Date()): string {
  const end = typeof deadline === "string" ? new Date(deadline) : deadline;
  const ms = end.getTime() - now.getTime();
  if (ms <= 0) return t("shared:format.daysLeft.expired");
  const days = Math.floor(ms / 86_400_000);
  return days === 0 ? t("shared:format.daysLeft.dueToday") : t("shared:format.daysLeft.count", { count: days });
}

/** Day-level countdown: start + days; null without a start. */
export function formatDaysLeft(
  startedAt: string | null | undefined,
  totalDays: number,
  t: SharedT,
  now: Date = new Date(),
): string | null {
  if (!startedAt) return null;
  return formatDaysUntil(new Date(new Date(startedAt).getTime() + totalDays * 86_400_000), t, now);
}

/** Subscription price: "2298.24" + month + 1 → "¥2,298.24/month"; count > 1 → "¥6,894.72/3 months"; unknown periods return the amount only. */
export function formatPeriodPrice(
  amount: string,
  period: string,
  count: number,
  t: SharedT,
  locale: string,
  currency: string | null,
): string {
  const price = formatMoney(amount, locale, currency);
  if (!isBillingPeriod(period)) return price;
  const unit = t(`shared:format.periodUnit.${period}`, { count });
  return count > 1
    ? t("shared:format.perPeriodCount", { price, count, unit })
    : t("shared:format.perPeriod", { price, unit });
}

/** Subscription expiry countdown: "23 days left" / "Due today" / "Expired"; null without an expiry instant. */
export function formatExpiry(expiresAt: string | null | undefined, t: SharedT, now: Date = new Date()): string | null {
  return expiresAt ? formatDaysUntil(expiresAt, t, now) : null;
}

/** The format function set of useFormat() bound to t, locale and the deployment currency. */
export interface Formatters {
  /** ISO 4217 code of the deployment currency; null until site config is known. */
  currency: string | null;
  /** Fraction digits of the currency (InputNumber precision / step). */
  minorUnits: number;
  currencySymbol: string;
  formatMoney: (amount: string) => string;
  formatPrice: (price: string) => string;
  formatHourlyPrice: (price: string) => string;
  formatDuration: (seconds: number) => string;
  formatCountdown: (deadline: string | Date, now?: Date) => string;
  formatReclaimCountdown: (deadline: string | Date, now?: Date) => string;
  formatDaysUntil: (deadline: string | Date, now?: Date) => string;
  formatDaysLeft: (startedAt: string | null | undefined, totalDays: number, now?: Date) => string | null;
  formatPeriodPrice: (amount: string, period: string, count: number) => string;
  formatExpiry: (expiresAt: string | null | undefined, now?: Date) => string | null;
  formatSpotDiscount: (discountPct: number) => string;
}

export function makeFormatters(t: SharedT, locale: string, currency: string | null): Formatters {
  return {
    currency,
    minorUnits: minorUnitsOf(currency),
    currencySymbol: currencySymbol(locale, currency),
    formatMoney: (amount) => formatMoney(amount, locale, currency),
    formatPrice: (price) => formatPrice(price, locale, currency),
    formatHourlyPrice: (price) => formatHourlyPrice(price, t, locale, currency),
    formatDuration: (seconds) => formatDuration(seconds, t),
    formatCountdown: (deadline, now) => formatCountdown(deadline, t, now),
    formatReclaimCountdown: (deadline, now) => formatReclaimCountdown(deadline, t, now),
    formatDaysUntil: (deadline, now) => formatDaysUntil(deadline, t, now),
    formatDaysLeft: (startedAt, totalDays, now) => formatDaysLeft(startedAt, totalDays, t, now),
    formatPeriodPrice: (amount, period, count) => formatPeriodPrice(amount, period, count, t, locale, currency),
    formatExpiry: (expiresAt, now) => formatExpiry(expiresAt, t, now),
    formatSpotDiscount: (discountPct) => formatSpotDiscount(discountPct, t, locale),
  };
}

/** Compare amount strings (BigInt, 4 dp): a<b → -1, a==b → 0, a>b → 1. */
export function compareAmounts(a: string, b: string): number {
  const d = scaleAmount(a, 4) - scaleAmount(b, 4);
  return d < 0n ? -1 : d > 0n ? 1 : 0;
}

/** Amount string → integer number in ten-thousandths (chart values, same basis as compareAmounts). */
export function amountToScaledNumber(s: string): number {
  return Number(scaleAmount(s, 4));
}

/** Add two amount strings (BigInt, 2 dp). Display layer only. */
export function addAmounts(a: string, b: string): string {
  return unscale(scaleAmount(a, 2) + scaleAmount(b, 2), 2);
}

/** Two-digit zero padding (local date-time assembly). */
const pad2 = (n: number) => String(n).padStart(2, "0");

/** Local "today" (YYYY-MM-DD) and time-zone offset (minutes, east of UTC positive): parameters of the daily consumption query. */
export function localToday(now: Date = new Date()): { date: string; tzOffsetMinutes: number } {
  return {
    date: `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`,
    tzOffsetMinutes: -now.getTimezoneOffset(),
  };
}

/** Time-zone suffix: "(UTC+8)" / "(UTC-5)" / "(UTC+5:30)". */
function tzSuffix(d: Date = new Date()): string {
  const offsetMin = -d.getTimezoneOffset();
  const sign = offsetMin >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMin);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  return `(UTC${sign}${h}${m ? `:${pad2(m)}` : ""})`;
}

/** ISO time → local date-time with the UTC offset; "-" for empty values. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())} ${tzSuffix(d)}`;
}

/** ISO time → local YYYY-MM-DD; "-" for empty values. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/** GB size → "100 GB" / "1.5 TB" */
export function formatSizeGb(gb: number): string {
  if (gb >= 1024) {
    const tb = gb / 1024;
    return `${Number.isInteger(tb) ? tb : tb.toFixed(1)} TB`;
  }
  return `${gb} GB`;
}

/** Mask a login handle the way the API does: email → first local-part character + `***@domain`;
 *  phone (E.164 or bare digits) → first 3 + `****` + last 4; anything else → `***`. */
export function maskHandle(handle: string | null | undefined): string {
  if (!handle) return "***";
  const at = handle.indexOf("@");
  if (at >= 0) {
    return `${handle.slice(0, 1)}***@${handle.slice(at + 1)}`;
  }
  return handle.length >= 7 ? `${handle.slice(0, 3)}****${handle.slice(-4)}` : "***";
}
