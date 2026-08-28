/**
 * 金额/时长/倒计时统一格式化。
 * 金额入参为后端 numeric 序列化出的字符串,禁止在前端做浮点运算;locale 只决定符号与量词。
 * 零外部依赖:t 由调用方显式传入(应用侧经 useFormat() 绑定,见各 app lib/format.ts)。
 */

import { isBillingPeriod, type BillingPeriod } from "./status";

/** 本包量词/单位文案用到的 key 全集(值在 locales 下两语言的 shared.json,由 locales.test 守护)。 */
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

/** 业务货币恒为人民币;en 语境用 CN¥ 避免被读作日元。 */
export function currencySymbol(locale: string): string {
  return locale.startsWith("zh") ? "¥" : "CN¥";
}

/** "1234.5" → "¥1,234.50"(zh)/ "CN¥1,234.50"(en),负数符号在最前。
 *  null/undefined 一律渲染为零;「数据未就绪」必须由调用方套 moneyOr 显示 "—"。 */
export function formatMoney(amount: string | null | undefined, locale: string): string {
  const currency = currencySymbol(locale);
  if (amount == null || amount === "") return `${currency}0.00`;
  const neg = amount.startsWith("-");
  const abs = neg ? amount.slice(1) : amount;
  const [intRaw = "0", fracRaw = ""] = abs.split(".");
  const frac = (fracRaw + "00").slice(0, 2);
  const int = intRaw.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${neg ? "-" : ""}${currency}${int}.${frac}`;
}

/** 每小时单价:"1.68" → "¥1.68/时" / "CN¥1.68/hr"(最多 4 位小数,去尾零但至少 2 位)。 */
export function formatHourlyPrice(price: string | null | undefined, t: SharedT, locale: string): string {
  const [int = "0", fracRaw = ""] = (price ?? "0").split(".");
  let frac = (fracRaw + "00").slice(0, 4).replace(/0+$/, "");
  if (frac.length < 2) frac = (frac + "00").slice(0, 2);
  return t("shared:format.perHour", { price: `${currencySymbol(locale)}${int}.${frac}` });
}

/** 非负十进制字符串 → 万分位 BigInt(禁浮点)。空值按 0 处理。 */
function scaled4(value: string | null | undefined): bigint {
  if (!value) return 0n;
  const [int = "0", frac = ""] = value.split(".");
  return BigInt(int + (frac + "0000").slice(0, 4));
}

/** 万分位 BigInt → "X.XXXX" */
function unscale4(scaled: bigint): string {
  const s = scaled.toString().padStart(5, "0");
  return `${s.slice(0, -4)}.${s.slice(-4)}`;
}

/** 分 BigInt → "X.XX" */
function unscale2(cents: bigint): string {
  const s = cents.toString().padStart(3, "0");
  return `${s.slice(0, -2)}.${s.slice(-2)}`;
}

/** 十进制字符串 × 整数(BigInt 精确到 4 位小数,禁浮点)。展示层用;计费权威在后端。 */
export function mulPrice(price: string | null | undefined, count: number): string {
  return unscale4(scaled4(price) * BigInt(count));
}

/** 「约 ¥X/日」估算:GB·月单价 × GB ÷ 30(BigInt 万分位中间值,HALF_EVEN 到分,与后端 as_amount 同舍入)。
 *  展示层估算,入账以后端日结为准。 */
export function diskDailyEstimate(priceGbMonth: string | null | undefined, gb: number): string {
  if (!priceGbMonth || gb <= 0 || !Number.isInteger(gb)) return "0.00";
  const [int = "0", frac = ""] = priceGbMonth.split(".");
  const monthlyScaled = BigInt(int + (frac + "0000").slice(0, 4)) * BigInt(gb); // 万分位
  // ÷30(天)÷100(万分位→分)
  const cents = halfEvenDiv(monthlyScaled, 3000n);
  const s = cents.toString().padStart(3, "0");
  return `${s.slice(0, -2)}.${s.slice(-2)}`;
}

/** 非负整数除法,余数恰为一半时向偶数进位(与后端 ROUND_HALF_EVEN 同语义)。 */
function halfEvenDiv(numerator: bigint, denominator: bigint): bigint {
  const q = numerator / denominator;
  const twice = (numerator - q * denominator) * 2n;
  if (twice > denominator) return q + 1n;
  if (twice < denominator) return q;
  return q % 2n === 0n ? q : q + 1n;
}

/** 周期长度取定长小时(与后端 core/pricing.py 的 PERIOD_HOURS 逐值一致);到期时刻与定价同源。 */
export const PERIOD_HOURS: Record<BillingPeriod, number> = {
  day: 24,
  week: 24 * 7,
  month: 24 * 30,
  year: 24 * 365,
};

/** 单次下单/续费的周期数上限(与后端 pricing.MAX_PERIOD_COUNT 一致)。 */
export const MAX_PERIOD_COUNT = 36;

/** 一小时收几份 price_hourly(与后端 money.billing_units 同口径:CPU 实例恒 1 份)。 */
export function billingUnits(gpuCount: number): number {
  return gpuCount || 1;
}

/** 包周期报价的展示副本(字段与后端 SubscriptionQuoteOut 同名同序)。
 *  只用于下单/续费前的预览,成交金额一律以接口返回的 quote 为准。 */
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

/** 本地报价(BigInt 全程精确,禁浮点)。运算顺序必须与后端 pricing.quote_subscription 一致:
 *  折后时价先量化到 4 位,再乘份数与小时数量化到分,换顺序会在边界差出分。
 *  base 一律取后端下单用的那个数:下单是 SKU 现价,续费是 `subscription.unit_price`。 */
export function quoteSubscription(
  baseHourly: string | null | undefined,
  opts: { units: number; period: BillingPeriod; periodCount: number; discountPct: number },
): PeriodQuote {
  const { units, period, periodCount, discountPct } = opts;
  const hours = PERIOD_HOURS[period] * periodCount;
  const base4 = scaled4(baseHourly);
  const unit4 = halfEvenDiv(base4 * BigInt(discountPct), 100n);
  const factor = BigInt(units) * BigInt(hours);
  // 万分位 × 份数 × 小时 → 分:再除 100
  const listCents = halfEvenDiv(base4 * factor, 100n);
  const amountCents = halfEvenDiv(unit4 * factor, 100n);
  return {
    period,
    periodCount,
    hours,
    discountPct,
    baseHourly: unscale4(base4),
    unitPrice: unscale4(unit4),
    listAmount: unscale2(listCents),
    discountAmount: unscale2(listCents - amountCents),
    amount: unscale2(amountCents),
  };
}

/** 竞价时价 = 按量时价 × `spot_discount_pct` / 100(BigInt 万分位,HALF_EVEN 到 4 位),
 *  与后端 `pricing.effective_price_hourly` 逐值一致。折扣只从 `/policies` 现算,不许硬编码。 */
export function spotHourlyPrice(
  baseHourly: string | null | undefined,
  discountPct: number,
): string {
  return unscale4(halfEvenDiv(scaled4(baseHourly) * BigInt(discountPct), 100n));
}

/** 折扣力度的本地化短语:zh 说「4 折」,en 说「40% of on-demand」。
 *  「折」按剩下几成算,与 en 的 % off 不是同一个数(4 折 ≠ 4% off);调用方当一段值嵌进整句。 */
export function formatSpotDiscount(discountPct: number, t: SharedT, locale: string): string {
  if (!locale.startsWith("zh")) return t("shared:format.spotDiscount", { off: discountPct });
  const whole = Math.trunc(discountPct / 10);
  const rest = discountPct % 10;
  return t("shared:format.spotDiscount", { off: rest === 0 ? `${whole}` : `${whole}.${rest}` });
}

/** 秒 → "X 小时 Y 分"(en 用缩写单位规避复数形态)。 */
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

/** 截止时间 → "剩 47h" / "剩 30m" / "已到期"。冻结回收倒计时统一格式。 */
export function formatCountdown(deadline: string | Date, t: SharedT, now: Date = new Date()): string {
  const end = typeof deadline === "string" ? new Date(deadline) : deadline;
  const ms = end.getTime() - now.getTime();
  if (ms <= 0) return t("shared:format.countdown.expired");
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return t("shared:format.countdown.hours", { count: hours });
  return t("shared:format.countdown.minutes", { count: Math.max(1, Math.floor(ms / 60_000)) });
}

/** 冻结行内标签整句:"剩 47h后回收"(中文零形态拼接无法直译,单独成键)。 */
export function formatReclaimCountdown(deadline: string | Date, t: SharedT, now: Date = new Date()): string {
  const end = typeof deadline === "string" ? new Date(deadline) : deadline;
  const ms = end.getTime() - now.getTime();
  if (ms <= 0) return t("shared:format.countdown.reclaimNow");
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return t("shared:format.countdown.reclaimHours", { count: hours });
  return t("shared:format.countdown.reclaimMinutes", { count: Math.max(1, Math.floor(ms / 60_000)) });
}

/** 天级倒计时:截止时刻 → "剩 X 天" / "今日到期" / "已到期"(服务端已给出截止时刻时用)。 */
export function formatDaysUntil(deadline: string | Date, t: SharedT, now: Date = new Date()): string {
  const end = typeof deadline === "string" ? new Date(deadline) : deadline;
  const ms = end.getTime() - now.getTime();
  if (ms <= 0) return t("shared:format.daysLeft.expired");
  const days = Math.floor(ms / 86_400_000);
  return days === 0 ? t("shared:format.daysLeft.dueToday") : t("shared:format.daysLeft.count", { count: days });
}

/** 天级倒计时:起点 + 天数(存储宽限/冻结列用)。返回 null 表示起点缺失,由调用方兜底。 */
export function formatDaysLeft(
  startedAt: string | null | undefined,
  totalDays: number,
  t: SharedT,
  now: Date = new Date(),
): string | null {
  if (!startedAt) return null;
  return formatDaysUntil(new Date(new Date(startedAt).getTime() + totalDays * 86_400_000), t, now);
}

/** 包周期价:"2298.24" + month + 1 → "¥2,298.24/月";份数 > 1 → "¥6,894.72/3 月"。
 *  未知周期只回金额,不编造量词。 */
export function formatPeriodPrice(
  amount: string | null | undefined,
  period: string,
  count: number,
  t: SharedT,
  locale: string,
): string {
  const price = formatMoney(amount, locale);
  if (!isBillingPeriod(period)) return price;
  const unit = t(`shared:format.periodUnit.${period}`, { count });
  return count > 1
    ? t("shared:format.perPeriodCount", { price, count, unit })
    : t("shared:format.perPeriod", { price, unit });
}

/** 包周期到期倒计时:"剩 23 天" / "今日到期" / "已到期";到期时刻缺失(非包周期实例)返回 null。 */
export function formatExpiry(
  expiresAt: string | null | undefined,
  t: SharedT,
  now: Date = new Date(),
): string | null {
  return expiresAt ? formatDaysUntil(expiresAt, t, now) : null;
}

/** 应用侧经 useFormat() 一次绑定 t/locale 后使用的格式化件集合。 */
export interface Formatters {
  currencySymbol: string;
  formatMoney(amount: string | null | undefined): string;
  formatHourlyPrice(price: string | null | undefined): string;
  formatDuration(seconds: number): string;
  formatCountdown(deadline: string | Date, now?: Date): string;
  formatReclaimCountdown(deadline: string | Date, now?: Date): string;
  formatDaysUntil(deadline: string | Date, now?: Date): string;
  formatDaysLeft(startedAt: string | null | undefined, totalDays: number, now?: Date): string | null;
  formatPeriodPrice(amount: string | null | undefined, period: string, count: number): string;
  formatExpiry(expiresAt: string | null | undefined, now?: Date): string | null;
  formatSpotDiscount(discountPct: number): string;
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

/** 金额字符串 → BigInt 万分位(比较/缩放共用的唯一缩放口径,禁浮点)。 */
function scaledAmount(s: string | null | undefined): bigint {
  if (!s) return 0n;
  const neg = s.startsWith("-");
  const [int = "0", frac = ""] = (neg ? s.slice(1) : s).split(".");
  const v = BigInt(int + (frac + "0000").slice(0, 4));
  return neg ? -v : v;
}

/** 金额字符串比较(BigInt 万分位精度,禁浮点):a<b → -1,a==b → 0,a>b → 1。 */
export function compareAmounts(
  a: string | null | undefined,
  b: string | null | undefined,
): number {
  const d = scaledAmount(a) - scaledAmount(b);
  return d < 0n ? -1 : d > 0n ? 1 : 0;
}

/** 金额字符串 → 万分位整数 number(ECharts 等图表值用:与 compareAmounts 同口径,
 *  占比/排序完全精确;拒绝 parseFloat 的浮点误差,如 0.1+0.2 ≠ 0.3)。 */
export function amountToScaledNumber(s: string | null | undefined): number {
  return Number(scaledAmount(s));
}

/** 两个金额字符串相加(BigInt 分级精确,2 位小数,禁浮点)。展示层用。 */
export function addAmounts(a: string | null | undefined, b: string | null | undefined): string {
  const cents = (s: string | null | undefined): bigint => {
    if (!s) return 0n;
    const neg = s.startsWith("-");
    const [int = "0", frac = ""] = (neg ? s.slice(1) : s).split(".");
    const v = BigInt(int + (frac + "00").slice(0, 2));
    return neg ? -v : v;
  };
  const sum = cents(a) + cents(b);
  const neg = sum < 0n;
  const abs = (neg ? -sum : sum).toString().padStart(3, "0");
  return `${neg ? "-" : ""}${abs.slice(0, -2)}.${abs.slice(-2)}`;
}

/** 本地"今天"(YYYY-MM-DD)与时区偏移(分,UTC 以东为正)—— 当日消费查询参数。 */
export function localToday(now: Date = new Date()): { date: string; tzOffsetMinutes: number } {
  const pad = (n: number) => String(n).padStart(2, "0");
  return {
    date: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`,
    tzOffsetMinutes: -now.getTimezoneOffset(),
  };
}

/** 时区后缀:按运行时真实偏移渲染 "(UTC+8)" / "(UTC-5)" / "(UTC+5:30)"。 */
export function tzSuffix(d: Date = new Date()): string {
  const offsetMin = -d.getTimezoneOffset(); // getTimezoneOffset 以西为正,取反成 UTC 以东为正
  const sign = offsetMin >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMin);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  return `(UTC${sign}${h}${m ? `:${String(m).padStart(2, "0")}` : ""})`;
}

/** ISO 时间 → "2026-08-19 10:30 (UTC+8)"(后缀随浏览器本地时区) */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())} ${tzSuffix(d)}`;
}

/** ISO 时间 → "2026-09-03"(本地日期;到期日这类只关心哪一天的场景) */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** GB 容量 → "100 GB" / "1.5 TB" */
export function formatSizeGb(gb: number): string {
  if (gb >= 1024) {
    const tb = gb / 1024;
    return `${Number.isInteger(tb) ? tb : tb.toFixed(1)} TB`;
  }
  return `${gb} GB`;
}

/** 手机号脱敏(与后端 account.realname.mask_phone 同口径):前 3 + 后 4,短串全掩。 */
export function maskPhone(phone: string): string {
  return phone.length >= 7 ? `${phone.slice(0, 3)}****${phone.slice(-4)}` : "***";
}
