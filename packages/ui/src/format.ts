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

/** 十进制字符串 × 整数(BigInt 精确到 4 位小数,禁浮点)。展示层用;计费权威在后端。 */
export function mulPrice(price: string | null | undefined, count: number): string {
  if (!price) return "0.0000";
  const [int = "0", frac = ""] = price.split(".");
  const scaled = BigInt(int + (frac + "0000").slice(0, 4)) * BigInt(count);
  const s = scaled.toString().padStart(5, "0");
  return `${s.slice(0, -4)}.${s.slice(-4)}`;
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

/**
 * 天级倒计时:起点 + 天数 → "剩 X 天" / "今日到期" / "已到期"(存储宽限/冻结列用)。
 * 返回 null 表示起点缺失(调用方自行兜底)。
 */
export function formatDaysLeft(
  startedAt: string | null | undefined,
  totalDays: number,
  now: Date = new Date(),
): string | null {
  if (!startedAt) return null;
  const deadline = new Date(startedAt).getTime() + totalDays * 86_400_000;
  const ms = deadline - now.getTime();
  if (ms <= 0) return "已到期";
  const days = Math.floor(ms / 86_400_000);
  return days === 0 ? "今日到期" : `剩 ${days} 天`;
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
