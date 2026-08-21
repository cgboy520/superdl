/**
 * 金额/时长/倒计时统一格式化(ui-ux-spec §3.8)。
 * 金额入参为后端 numeric 序列化出的字符串,禁止在前端做浮点运算;locale 只决定符号与量词。
 * 本模块保持零运行时依赖:t 由调用方显式传入(应用侧经 useFormat() 绑定,见各 app lib/format.ts)。
 */

/** 本包量词/单位文案用到的 key 全集(值在 locales 下两语言的 shared.json,由 locales.test 守护)。 */
export type SharedFormatKey =
  | "shared:format.perHour"
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
  | "shared:format.daysLeft.count";

export type SharedT = (key: SharedFormatKey, opts?: Record<string, unknown>) => string;

/** 业务货币恒为人民币;en 语境用 CN¥ 避免被读作日元。 */
function currencySymbol(locale: string): string {
  return locale.startsWith("zh") ? "¥" : "CN¥";
}

/** "1234.5" → "¥1,234.50"(zh)/ "CN¥1,234.50"(en);负数符号在最前。 */
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

/** 十进制字符串 × 整数(BigInt 精确到 4 位小数,禁浮点)。展示层用;计费权威在后端。 */
export function mulPrice(price: string | null | undefined, count: number): string {
  if (!price) return "0.0000";
  const [int = "0", frac = ""] = price.split(".");
  const scaled = BigInt(int + (frac + "0000").slice(0, 4)) * BigInt(count);
  const s = scaled.toString().padStart(5, "0");
  return `${s.slice(0, -4)}.${s.slice(-4)}`;
}

/**
 * 「约 ¥X/日」估算:GB·月单价 × GB ÷ 30(BigInt 万分位中间值,HALF_UP 到分)。
 * 展示层估算;入账以后端日结(HALF_EVEN)为准。返回 "1.67" 形式的两位小数串。
 */
export function diskDailyEstimate(priceGbMonth: string | null | undefined, gb: number): string {
  if (!priceGbMonth || gb <= 0 || !Number.isInteger(gb)) return "0.00";
  const [int = "0", frac = ""] = priceGbMonth.split(".");
  const monthlyScaled = BigInt(int + (frac + "0000").slice(0, 4)) * BigInt(gb); // 万分位
  const cents = (monthlyScaled + 1500n) / 3000n; // ÷30(天)÷100(万分位→分),HALF_UP
  const s = cents.toString().padStart(3, "0");
  return `${s.slice(0, -2)}.${s.slice(-2)}`;
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

/**
 * 天级倒计时:起点 + 天数 → "剩 X 天" / "今日到期" / "已到期"(存储宽限/冻结列用)。
 * 返回 null 表示起点缺失(调用方自行兜底)。
 */
export function formatDaysLeft(
  startedAt: string | null | undefined,
  totalDays: number,
  t: SharedT,
  now: Date = new Date(),
): string | null {
  if (!startedAt) return null;
  const deadline = new Date(startedAt).getTime() + totalDays * 86_400_000;
  const ms = deadline - now.getTime();
  if (ms <= 0) return t("shared:format.daysLeft.expired");
  const days = Math.floor(ms / 86_400_000);
  return days === 0 ? t("shared:format.daysLeft.dueToday") : t("shared:format.daysLeft.count", { count: days });
}

/** 应用侧经 useFormat() 一次绑定 t/locale 后使用的格式化件集合。 */
export interface Formatters {
  formatMoney(amount: string | null | undefined): string;
  formatHourlyPrice(price: string | null | undefined): string;
  formatDuration(seconds: number): string;
  formatCountdown(deadline: string | Date, now?: Date): string;
  formatReclaimCountdown(deadline: string | Date, now?: Date): string;
  formatDaysLeft(startedAt: string | null | undefined, totalDays: number, now?: Date): string | null;
}

export function makeFormatters(t: SharedT, locale: string): Formatters {
  return {
    formatMoney: (amount) => formatMoney(amount, locale),
    formatHourlyPrice: (price) => formatHourlyPrice(price, t, locale),
    formatDuration: (seconds) => formatDuration(seconds, t),
    formatCountdown: (deadline, now) => formatCountdown(deadline, t, now),
    formatReclaimCountdown: (deadline, now) => formatReclaimCountdown(deadline, t, now),
    formatDaysLeft: (startedAt, totalDays, now) => formatDaysLeft(startedAt, totalDays, t, now),
  };
}

/** 金额字符串比较(BigInt 万分位精度,禁浮点):a<b → -1,a==b → 0,a>b → 1。 */
export function compareAmounts(
  a: string | null | undefined,
  b: string | null | undefined,
): number {
  const scaled = (s: string | null | undefined): bigint => {
    if (!s) return 0n;
    const neg = s.startsWith("-");
    const [int = "0", frac = ""] = (neg ? s.slice(1) : s).split(".");
    const v = BigInt(int + (frac + "0000").slice(0, 4));
    return neg ? -v : v;
  };
  const d = scaled(a) - scaled(b);
  return d < 0n ? -1 : d > 0n ? 1 : 0;
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

/** ISO 时间 → "2026-08-19 10:30" */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** GB 容量 → "100 GB" / "1.5 TB" */
export function formatSizeGb(gb: number): string {
  if (gb >= 1024) {
    const tb = gb / 1024;
    return `${Number.isInteger(tb) ? tb : tb.toFixed(1)} TB`;
  }
  return `${gb} GB`;
}
