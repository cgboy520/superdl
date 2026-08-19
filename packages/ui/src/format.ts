/**
 * 金额/时长/倒计时统一格式化(ui-ux-spec §3.8)。
 * 金额入参为后端 numeric 序列化出的字符串,禁止在前端做浮点运算。
 */

/** "1234.5" → "¥1,234.50";负数 → "-¥12.30" */
export function formatMoney(amount: string | null | undefined, currency = "¥"): string {
  if (amount == null || amount === "") return `${currency}0.00`;
  const neg = amount.startsWith("-");
  const abs = neg ? amount.slice(1) : amount;
  const [intRaw = "0", fracRaw = ""] = abs.split(".");
  const frac = (fracRaw + "00").slice(0, 2);
  const int = intRaw.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${neg ? "-" : ""}${currency}${int}.${frac}`;
}

/** 每小时单价:"1.68" → "¥1.68/时"(单价最多展示 4 位小数,去尾零但至少 2 位) */
export function formatHourlyPrice(price: string | null | undefined): string {
  if (price == null || price === "") return "¥0.00/时";
  const [int = "0", fracRaw = ""] = price.split(".");
  let frac = (fracRaw + "00").slice(0, 4).replace(/0+$/, "");
  if (frac.length < 2) frac = (frac + "00").slice(0, 2);
  return `¥${int}.${frac}/时`;
}

/** 秒 → "X 小时 Y 分"(不足 1 分钟显示"不足 1 分钟") */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return seconds <= 0 ? "0 分钟" : "不足 1 分钟";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h === 0) return `${m} 分钟`;
  if (m === 0) return `${h} 小时`;
  return `${h} 小时 ${m} 分`;
}

/** 截止时间 → "剩 47h" / "剩 30m" / "已到期"。冻结回收倒计时统一格式 */
export function formatCountdown(deadline: string | Date, now: Date = new Date()): string {
  const end = typeof deadline === "string" ? new Date(deadline) : deadline;
  const ms = end.getTime() - now.getTime();
  if (ms <= 0) return "已到期";
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return `剩 ${hours}h`;
  return `剩 ${Math.max(1, Math.floor(ms / 60_000))}m`;
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
