/** 金额/时长/倒计时格式化。金额入参是后端 numeric 字符串,禁浮点;t 由调用方传入(应用侧经 useFormat() 绑定)。 */

import { isBillingPeriod, type BillingPeriod } from "./status";

/** 本包用到的 shared.json 文案 key 全集(locales.test 守护)。 */
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

/** 货币恒为人民币;en 用 CN¥。 */
function currencySymbol(locale: string): string {
  return locale.startsWith("zh") ? "¥" : "CN¥";
}

/** "1234.5" → "¥1,234.50"(zh)/ "CN¥1,234.50"(en),负号在最前。入参是后端 numeric 字符串;查询未就绪走 moneyOr 显「—」,这里不接受 null(不显假 ¥0.00)。 */
export function formatMoney(amount: string, locale: string): string {
  const currency = currencySymbol(locale);
  const neg = amount.startsWith("-");
  const abs = neg ? amount.slice(1) : amount;
  const [intRaw = "0", fracRaw = ""] = abs.split(".");
  const frac = (fracRaw + "00").slice(0, 2);
  const int = intRaw.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${neg ? "-" : ""}${currency}${int}.${frac}`;
}

/** 时价:"1.68" → "¥1.68/时" / "CN¥1.68/hr"(2–4 位小数,去尾零)。 */
export function formatHourlyPrice(price: string, t: SharedT, locale: string): string {
  const [int = "0", fracRaw = ""] = price.split(".");
  let frac = (fracRaw + "00").slice(0, 4).replace(/0+$/, "");
  if (frac.length < 2) frac = (frac + "00").slice(0, 2);
  return t("shared:format.perHour", { price: `${currencySymbol(locale)}${int}.${frac}` });
}

/** 十进制字符串 → BigInt 定点(带符号;空串按 0)。digits = 小数位数(金额 2 / 单价 4)。 */
function scaleAmount(s: string, digits: 2 | 4): bigint {
  if (s === "") return 0n;
  const neg = s.startsWith("-");
  const [int = "0", frac = ""] = (neg ? s.slice(1) : s).split(".");
  const v = BigInt(int + (frac + "0000").slice(0, digits));
  return neg ? -v : v;
}

/** BigInt 定点 → 十进制字符串(带符号)。 */
function unscale(scaled: bigint, digits: 2 | 4): string {
  const neg = scaled < 0n;
  const s = (neg ? -scaled : scaled).toString().padStart(digits + 1, "0");
  return `${neg ? "-" : ""}${s.slice(0, -digits)}.${s.slice(-digits)}`;
}

/** 十进制字符串 × 整数(BigInt,4 位小数)。展示层用。 */
export function mulPrice(price: string, count: number): string {
  return unscale(scaleAmount(price, 4) * BigInt(count), 4);
}

/** 「约 ¥X/日」:GB·月单价 × GB ÷ 30,HALF_EVEN 到分(与后端 as_amount 同舍入)。展示层估算。 */
export function diskDailyEstimate(priceGbMonth: string, gb: number): string {
  if (gb <= 0 || !Number.isInteger(gb)) return "0.00";
  const monthlyScaled = scaleAmount(priceGbMonth, 4) * BigInt(gb);
  return unscale(halfEvenDiv(monthlyScaled, 3000n), 2);
}

/** 非负整数除法,ROUND_HALF_EVEN(与后端同语义)。 */
function halfEvenDiv(numerator: bigint, denominator: bigint): bigint {
  const q = numerator / denominator;
  const twice = (numerator - q * denominator) * 2n;
  if (twice > denominator) return q + 1n;
  if (twice < denominator) return q;
  return q % 2n === 0n ? q : q + 1n;
}

/** 周期定长小时,与后端 core/pricing.py PERIOD_HOURS 逐值一致。 */
export const PERIOD_HOURS: Record<BillingPeriod, number> = {
  day: 24,
  week: 24 * 7,
  month: 24 * 30,
  year: 24 * 365,
};

/** 单次下单/续费的周期数上限(与后端 pricing.MAX_PERIOD_COUNT 一致)。 */
export const MAX_PERIOD_COUNT = 36;

/** 一小时收几份 price_hourly(与后端 money.billing_units 同口径;CPU 实例恒 1)。 */
export function billingUnits(gpuCount: number): number {
  return gpuCount || 1;
}

/** 包周期报价(字段与后端 SubscriptionQuoteOut 同名同序)。仅预览,成交以接口 quote 为准。 */
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

/** 本地报价(BigInt)。运算顺序与后端 pricing.quote_subscription 一致:折后时价先量化到 4 位,再乘份数与小时数量化到分。
 *  base:下单取 SKU 现价,续费取 `subscription.unit_price`。 */
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

/** 竞价时价 = 按量时价 × `spot_discount_pct` / 100(HALF_EVEN 到 4 位,与后端 `pricing.price_for` 一致)。折扣只从 `/policies` 取。 */
export function spotHourlyPrice(baseHourly: string, discountPct: number): string {
  return unscale(halfEvenDiv(scaleAmount(baseHourly, 4) * BigInt(discountPct), 100n), 4);
}

/** 折扣短语:zh「4 折」,en「40% of on-demand」;调用方当一段值嵌进整句。 */
export function formatSpotDiscount(discountPct: number, t: SharedT, locale: string): string {
  if (!locale.startsWith("zh")) return t("shared:format.spotDiscount", { off: discountPct });
  const whole = Math.trunc(discountPct / 10);
  const rest = discountPct % 10;
  return t("shared:format.spotDiscount", { off: rest === 0 ? `${whole}` : `${whole}.${rest}` });
}

/** 秒 → "X 小时 Y 分"(en 用缩写单位)。 */
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

/** 截止时间 → "剩 47h" / "剩 30m" / "已到期"。 */
export function formatCountdown(deadline: string | Date, t: SharedT, now: Date = new Date()): string {
  const end = typeof deadline === "string" ? new Date(deadline) : deadline;
  const ms = end.getTime() - now.getTime();
  if (ms <= 0) return t("shared:format.countdown.expired");
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return t("shared:format.countdown.hours", { count: hours });
  return t("shared:format.countdown.minutes", { count: Math.max(1, Math.floor(ms / 60_000)) });
}

/** 冻结行内标签整句:"剩 47h后回收"(单独成键)。 */
export function formatReclaimCountdown(deadline: string | Date, t: SharedT, now: Date = new Date()): string {
  const end = typeof deadline === "string" ? new Date(deadline) : deadline;
  const ms = end.getTime() - now.getTime();
  if (ms <= 0) return t("shared:format.countdown.reclaimNow");
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return t("shared:format.countdown.reclaimHours", { count: hours });
  return t("shared:format.countdown.reclaimMinutes", { count: Math.max(1, Math.floor(ms / 60_000)) });
}

/** 天级倒计时:截止时刻 → "剩 X 天" / "今日到期" / "已到期"。 */
export function formatDaysUntil(deadline: string | Date, t: SharedT, now: Date = new Date()): string {
  const end = typeof deadline === "string" ? new Date(deadline) : deadline;
  const ms = end.getTime() - now.getTime();
  if (ms <= 0) return t("shared:format.daysLeft.expired");
  const days = Math.floor(ms / 86_400_000);
  return days === 0 ? t("shared:format.daysLeft.dueToday") : t("shared:format.daysLeft.count", { count: days });
}

/** 天级倒计时:起点 + 天数;起点缺失返回 null。 */
export function formatDaysLeft(
  startedAt: string | null | undefined,
  totalDays: number,
  t: SharedT,
  now: Date = new Date(),
): string | null {
  if (!startedAt) return null;
  return formatDaysUntil(new Date(new Date(startedAt).getTime() + totalDays * 86_400_000), t, now);
}

/** 包周期价:"2298.24" + month + 1 → "¥2,298.24/月";份数 > 1 → "¥6,894.72/3 月";未知周期只回金额。 */
export function formatPeriodPrice(amount: string, period: string, count: number, t: SharedT, locale: string): string {
  const price = formatMoney(amount, locale);
  if (!isBillingPeriod(period)) return price;
  const unit = t(`shared:format.periodUnit.${period}`, { count });
  return count > 1
    ? t("shared:format.perPeriodCount", { price, count, unit })
    : t("shared:format.perPeriod", { price, unit });
}

/** 包周期到期倒计时:"剩 23 天" / "今日到期" / "已到期";无到期时刻返回 null。 */
export function formatExpiry(expiresAt: string | null | undefined, t: SharedT, now: Date = new Date()): string | null {
  return expiresAt ? formatDaysUntil(expiresAt, t, now) : null;
}

/** useFormat() 绑定 t/locale 后的格式化件集合。属性式签名(非方法简写),解构不触 unbound-method。 */
export interface Formatters {
  currencySymbol: string;
  formatMoney: (amount: string) => string;
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

export function makeFormatters(t: SharedT, locale: string): Formatters {
  return {
    currencySymbol: currencySymbol(locale),
    formatMoney: (amount) => formatMoney(amount, locale),
    formatHourlyPrice: (price) => formatHourlyPrice(price, t, locale),
    formatDuration: (seconds) => formatDuration(seconds, t),
    formatCountdown: (deadline, now) => formatCountdown(deadline, t, now),
    formatReclaimCountdown: (deadline, now) => formatReclaimCountdown(deadline, t, now),
    formatDaysUntil: (deadline, now) => formatDaysUntil(deadline, t, now),
    formatDaysLeft: (startedAt, totalDays, now) => formatDaysLeft(startedAt, totalDays, t, now),
    formatPeriodPrice: (amount, period, count) => formatPeriodPrice(amount, period, count, t, locale),
    formatExpiry: (expiresAt, now) => formatExpiry(expiresAt, t, now),
    formatSpotDiscount: (discountPct) => formatSpotDiscount(discountPct, t, locale),
  };
}

/** 金额字符串比较(BigInt 万分位):a<b → -1,a==b → 0,a>b → 1。 */
export function compareAmounts(a: string, b: string): number {
  const d = scaleAmount(a, 4) - scaleAmount(b, 4);
  return d < 0n ? -1 : d > 0n ? 1 : 0;
}

/** 金额字符串 → 万分位整数 number(图表值用,与 compareAmounts 同口径)。 */
export function amountToScaledNumber(s: string): number {
  return Number(scaleAmount(s, 4));
}

/** 两个金额字符串相加(BigInt,2 位小数)。展示层用。 */
export function addAmounts(a: string, b: string): string {
  return unscale(scaleAmount(a, 2) + scaleAmount(b, 2), 2);
}

/** 两位零填充(本地日期时间拼接用)。 */
const pad2 = (n: number) => String(n).padStart(2, "0");

/** 本地"今天"(YYYY-MM-DD)与时区偏移(分,UTC 以东为正):当日消费查询参数。 */
export function localToday(now: Date = new Date()): { date: string; tzOffsetMinutes: number } {
  return {
    date: `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`,
    tzOffsetMinutes: -now.getTimezoneOffset(),
  };
}

/** 时区后缀:"(UTC+8)" / "(UTC-5)" / "(UTC+5:30)"。 */
function tzSuffix(d: Date = new Date()): string {
  const offsetMin = -d.getTimezoneOffset(); // getTimezoneOffset 以西为正,取反成 UTC 以东为正
  const sign = offsetMin >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMin);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  return `(UTC${sign}${h}${m ? `:${pad2(m)}` : ""})`;
}

/** ISO 时间 → "2026-08-19 10:30 (UTC+8)"(浏览器本地时区) */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())} ${tzSuffix(d)}`;
}

/** ISO 时间 → "2026-09-03"(本地日期) */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/** GB 容量 → "100 GB" / "1.5 TB" */
export function formatSizeGb(gb: number): string {
  if (gb >= 1024) {
    const tb = gb / 1024;
    return `${Number.isInteger(tb) ? tb : tb.toFixed(1)} TB`;
  }
  return `${gb} GB`;
}

/** 手机号脱敏:前 3 + 后 4,短串全掩(与后端 core/logging.py mask_phone_value 同口径)。 */
export function maskPhone(phone: string): string {
  return phone.length >= 7 ? `${phone.slice(0, 3)}****${phone.slice(-4)}` : "***";
}
