import { createInstance } from "i18next";
import { describe, expect, it } from "vitest";

import enShared from "../locales/en-US/shared.json";
import zhShared from "../locales/zh-CN/shared.json";
import {
  addAmounts,
  compareAmounts,
  diskDailyEstimate,
  formatCountdown,
  formatDate,
  formatDateTime,
  formatDaysLeft,
  formatDuration,
  formatExpiry,
  formatHourlyPrice,
  formatMoney,
  formatPeriodPrice,
  formatReclaimCountdown,
  formatSizeGb,
  formatSpotDiscount,
  maskHandle,
  mulPrice,
  quoteSubscription,
  spotHourlyPrice,
  type SharedT,
} from "./format";

/** 真实 i18next 实例渲染 shared.json。 */
function makeT(lng: "zh-CN" | "en-US"): SharedT {
  const inst = createInstance({
    lng,
    fallbackLng: "zh-CN",
    resources: {
      "zh-CN": { shared: zhShared },
      "en-US": { shared: enShared },
    },
    interpolation: { escapeValue: false },
  });
  void inst.init();
  return (key: string, opts?: Record<string, unknown>) => inst.t(key, opts);
}
const tZh = makeT("zh-CN");
const tEn = makeT("en-US");

describe("addAmounts", () => {
  it("BigInt 精确相加无浮点误差", () => {
    expect(addAmounts("1.10", "2.80")).toBe("3.90");
    expect(addAmounts("0.01", "0.02")).toBe("0.03");
  });
  it("负数", () => {
    expect(addAmounts("-1.50", "1.00")).toBe("-0.50");
  });
});

describe("maskHandle", () => {
  it("phones keep 3 + 4, emails keep the first character and the domain, junk is fully masked", () => {
    expect(maskHandle("+8613812345678")).toBe("+86****5678");
    expect(maskHandle("13812345678")).toBe("138****5678");
    expect(maskHandle("alice@example.com")).toBe("a***@example.com");
    expect(maskHandle("12345")).toBe("***");
    expect(maskHandle("")).toBe("***");
    expect(maskHandle(null)).toBe("***");
  });
});

describe("compareAmounts", () => {
  it("BigInt 精确比较无浮点误差", () => {
    expect(compareAmounts("0.30", "0.3000")).toBe(0);
    expect(compareAmounts("100.00", "99.9999")).toBe(1);
    expect(compareAmounts("1.6799", "1.68")).toBe(-1);
    expect(compareAmounts("0.3000", "0.2999")).toBe(1);
  });
  it("负数", () => {
    expect(compareAmounts("-0.01", "0")).toBe(-1);
  });
});

describe("mulPrice", () => {
  it("BigInt 精确乘法无浮点误差", () => {
    expect(mulPrice("1.9900", 2)).toBe("3.9800");
    expect(mulPrice("0.98", 3)).toBe("2.9400");
    expect(mulPrice("0.0001", 8)).toBe("0.0008");
  });
});

describe.each([
  ["zh-CN", tZh],
  ["en-US", tEn],
] as const)("formatDaysLeft (%s)", (lng, t) => {
  const now = new Date("2026-08-19T12:00:00Z");
  const zh = lng === "zh-CN";
  it("宽限期剩余天数(en 复数)", () => {
    expect(formatDaysLeft("2026-08-17T00:00:00Z", 7, t, now)).toBe(zh ? "剩 4 天" : "4 days left");
  });
  it("剩 1 天(en 单数边界)", () => {
    expect(formatDaysLeft("2026-08-14T00:00:00Z", 7, t, now)).toBe(zh ? "剩 1 天" : "1 day left");
  });
  it("最后一天显示今日到期", () => {
    expect(formatDaysLeft("2026-08-12T20:00:00Z", 7, t, now)).toBe(zh ? "今日到期" : "Due today");
  });
  it("已越过截止", () => {
    expect(formatDaysLeft("2026-08-01T00:00:00Z", 7, t, now)).toBe(zh ? "已到期" : "Expired");
  });
});

it("formatDaysLeft 起点缺失返回 null(与语言无关)", () => {
  expect(formatDaysLeft(null, 7, tZh)).toBeNull();
  expect(formatDaysLeft(undefined, 30, tZh)).toBeNull();
});

describe("formatMoney", () => {
  it("zh:千分位与两位小数", () => {
    expect(formatMoney("1234.5", "zh-CN")).toBe("¥1,234.50");
    expect(formatMoney("0", "zh-CN")).toBe("¥0.00");
  });
  it("en:CN¥ 符号避免日元歧义", () => {
    expect(formatMoney("1234.5", "en-US")).toBe("CN¥1,234.50");
    expect(formatMoney("-12.3", "en-US")).toBe("-CN¥12.30");
  });
  it("截断而非四舍五入(展示层不做算术)", () => {
    expect(formatMoney("1.999", "zh-CN")).toBe("¥1.99");
  });
});

describe("formatHourlyPrice", () => {
  it("zh:保留有效小数,至少两位", () => {
    expect(formatHourlyPrice("1.6800", tZh, "zh-CN")).toBe("¥1.68/时");
    expect(formatHourlyPrice("0.1250", tZh, "zh-CN")).toBe("¥0.125/时");
    expect(formatHourlyPrice("3", tZh, "zh-CN")).toBe("¥3.00/时");
  });
  it("en:/hr 量词", () => {
    expect(formatHourlyPrice("1.6800", tEn, "en-US")).toBe("CN¥1.68/hr");
  });
});

describe.each([
  ["zh-CN", tZh],
  ["en-US", tEn],
] as const)("formatDuration (%s)", (lng, t) => {
  const zh = lng === "zh-CN";
  it("小时+分钟", () => {
    expect(formatDuration(3660, t)).toBe(zh ? "1 小时 1 分" : "1 hr 1 min");
    expect(formatDuration(7200, t)).toBe(zh ? "2 小时" : "2 hr");
    expect(formatDuration(120, t)).toBe(zh ? "2 分钟" : "2 min");
    expect(formatDuration(30, t)).toBe(zh ? "不足 1 分钟" : "less than a minute");
    expect(formatDuration(0, t)).toBe(zh ? "0 分钟" : "0 min");
  });
});

describe.each([
  ["zh-CN", tZh],
  ["en-US", tEn],
] as const)("formatCountdown (%s)", (lng, t) => {
  const now = new Date("2026-08-19T00:00:00Z");
  const zh = lng === "zh-CN";
  it("小时级", () => {
    expect(formatCountdown(new Date("2026-08-20T23:00:00Z"), t, now)).toBe(zh ? "剩 47h" : "47h left");
  });
  it("分钟级", () => {
    expect(formatCountdown(new Date("2026-08-19T00:30:00Z"), t, now)).toBe(zh ? "剩 30m" : "30m left");
  });
  it("已到期", () => {
    expect(formatCountdown(new Date("2026-08-18T00:00:00Z"), t, now)).toBe(zh ? "已到期" : "Expired");
  });
  it("冻结行内整句(中文零形态拼接单独成键)", () => {
    expect(formatReclaimCountdown(new Date("2026-08-20T23:00:00Z"), t, now)).toBe(
      zh ? "剩 47h后回收" : "reclaimed in 47h",
    );
    expect(formatReclaimCountdown(new Date("2026-08-19T00:30:00Z"), t, now)).toBe(
      zh ? "剩 30m后回收" : "reclaimed in 30m",
    );
    expect(formatReclaimCountdown(new Date("2026-08-18T00:00:00Z"), t, now)).toBe(zh ? "即将回收" : "reclaiming soon");
  });
});

describe("formatSizeGb", () => {
  it("GB 与 TB", () => {
    expect(formatSizeGb(100)).toBe("100 GB");
    expect(formatSizeGb(1024)).toBe("1 TB");
    expect(formatSizeGb(1536)).toBe("1.5 TB");
  });
});

describe("formatDateTime 时区后缀", () => {
  it("输出带 (UTC±x) 后缀,与运行时偏移一致", () => {
    const out = formatDateTime("2026-08-19T02:30:00Z");
    expect(out).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2} \(UTC[+-]\d+(:\d{2})?\)$/);
    const d = new Date("2026-08-19T02:30:00Z");
    const offsetMin = -d.getTimezoneOffset();
    const sign = offsetMin >= 0 ? "+" : "-";
    const abs = Math.abs(offsetMin);
    const suffix = `(UTC${sign}${Math.floor(abs / 60)}${abs % 60 ? `:${String(abs % 60).padStart(2, "0")}` : ""})`;
    expect(out.endsWith(suffix)).toBe(true);
  });
  it("空值仍为占位符", () => {
    expect(formatDateTime(null)).toBe("-");
    expect(formatDateTime(undefined)).toBe("-");
  });
});

describe("diskDailyEstimate", () => {
  it("月价折日价 HALF_EVEN 到分", () => {
    expect(diskDailyEstimate("0.5000", 100)).toBe("1.67");
    expect(diskDailyEstimate("0.5000", 60)).toBe("1.00");
    expect(diskDailyEstimate("0.1000", 10)).toBe("0.03");
    expect(diskDailyEstimate("1.2345", 30)).toBe("1.23");
  });
  it("分位 tie 向偶,与后端 as_amount 同语义", () => {
    expect(diskDailyEstimate("0.0350", 30)).toBe("0.04");
    expect(diskDailyEstimate("0.0350", 90)).toBe("0.10");
    expect(diskDailyEstimate("0.0500", 15)).toBe("0.02");
  });
  it("边界:空价/0GB/非整数 GB 返回 0.00", () => {
    expect(diskDailyEstimate("", 100)).toBe("0.00");
    expect(diskDailyEstimate("0.50", 0)).toBe("0.00");
    expect(diskDailyEstimate("0.50", 1.5)).toBe("0.00");
  });
});

describe("quoteSubscription", () => {
  it("逐步量化与后端 quote_subscription 对齐(折后时价先到 4 位,再乘份数与小时到分)", () => {
    const q = quoteSubscription("3.9900", { units: 1, period: "month", periodCount: 1, discountPct: 80 });
    expect(q.hours).toBe(720);
    expect(q.unitPrice).toBe("3.1920");
    expect(q.listAmount).toBe("2872.80");
    expect(q.discountAmount).toBe("574.56");
    expect(q.amount).toBe("2298.24");
  });
  it("三件套自洽:discountAmount === listAmount - amount", () => {
    for (const pct of [95, 90, 80, 70]) {
      for (const period of ["day", "week", "month", "year"] as const) {
        const q = quoteSubscription("1.2345", { units: 3, period, periodCount: 2, discountPct: pct });
        expect(addAmounts(q.amount, q.discountAmount)).toBe(q.listAmount);
      }
    }
  });
  it("份数与周期数是乘数:CPU 实例(units=1)不按卡放大", () => {
    const one = quoteSubscription("2.0000", { units: 1, period: "day", periodCount: 1, discountPct: 95 });
    const four = quoteSubscription("2.0000", { units: 4, period: "day", periodCount: 1, discountPct: 95 });
    expect(one.amount).toBe("45.60");
    expect(four.amount).toBe("182.40");
  });
  it("年付 8760 小时不溢出、不丢精度", () => {
    const q = quoteSubscription("3.9900", { units: 8, period: "year", periodCount: 3, discountPct: 70 });
    expect(q.hours).toBe(26280);
    expect(q.unitPrice).toBe("2.7930");
    expect(q.listAmount).toBe("838857.60");
    expect(q.amount).toBe("587200.32");
  });
  it("100% 折扣(不打折)照常自洽", () => {
    const full = quoteSubscription("1.0000", { units: 1, period: "day", periodCount: 1, discountPct: 100 });
    expect(full.amount).toBe(full.listAmount);
    expect(full.discountAmount).toBe("0.00");
  });
});

describe.each([
  ["zh-CN", tZh],
  ["en-US", tEn],
] as const)("formatPeriodPrice (%s)", (lng, t) => {
  const zh = lng === "zh-CN";
  const locale = lng;
  it("单个周期", () => {
    expect(formatPeriodPrice("2298.24", "month", 1, t, locale)).toBe(zh ? "¥2,298.24/月" : "CN¥2,298.24/month");
  });
  it("多个周期(en 走复数量词)", () => {
    expect(formatPeriodPrice("6894.72", "month", 3, t, locale)).toBe(
      zh ? "¥6,894.72/3 月" : "CN¥6,894.72 per 3 months",
    );
  });
  it("未知周期只回金额,不编造量词", () => {
    expect(formatPeriodPrice("10.00", "quarter", 1, t, locale)).toBe(zh ? "¥10.00" : "CN¥10.00");
  });
});

describe("spotHourlyPrice", () => {
  it("与后端 as_price(price × pct / 100) 同口径:HALF_EVEN 量化到 4 位", () => {
    expect(spotHourlyPrice("3.9900", 40)).toBe("1.5960");
    expect(spotHourlyPrice("3.9900", 100)).toBe("3.9900");
  });
  it("恰好半个万分位时向偶进(与 quoteSubscription 的折后时价同一条舍入规则)", () => {
    expect(spotHourlyPrice("0.0010", 45)).toBe("0.0004");
    expect(spotHourlyPrice("0.0030", 45)).toBe("0.0014");
    expect(spotHourlyPrice("0.0001", 45)).toBe("0.0000");
  });
  it("与 quoteSubscription 的 unitPrice 逐值一致(同一个折扣算法,不能有两套)", () => {
    for (const pct of [10, 40, 45, 55, 90]) {
      const q = quoteSubscription("1.2345", { units: 1, period: "day", periodCount: 1, discountPct: pct });
      expect(spotHourlyPrice("1.2345", pct)).toBe(q.unitPrice);
    }
  });
});

describe("formatSpotDiscount", () => {
  const zh = makeT("zh-CN");
  const en = makeT("en-US");
  it("zh 按「折」说(40 → 4 折),整十不留小数点", () => {
    expect(formatSpotDiscount(40, zh, "zh-CN")).toBe("4 折");
    expect(formatSpotDiscount(90, zh, "zh-CN")).toBe("9 折");
  });
  it("zh 非整十保留一位小数(45 → 4.5 折)", () => {
    expect(formatSpotDiscount(45, zh, "zh-CN")).toBe("4.5 折");
    expect(formatSpotDiscount(10, zh, "zh-CN")).toBe("1 折");
  });
  it("en 直接说占按量价的百分比(「4 折」在英文里没有对应说法)", () => {
    expect(formatSpotDiscount(40, en, "en-US")).toBe("40% of on-demand");
    expect(formatSpotDiscount(45, en, "en-US")).toBe("45% of on-demand");
  });
});

describe("formatExpiry", () => {
  const now = new Date("2026-08-19T12:00:00Z");
  it("剩余天数 / 今日到期 / 已到期", () => {
    expect(formatExpiry("2026-09-11T12:00:00Z", tZh, now)).toBe("剩 23 天");
    expect(formatExpiry("2026-08-19T20:00:00Z", tZh, now)).toBe("今日到期");
    expect(formatExpiry("2026-08-18T12:00:00Z", tZh, now)).toBe("已到期");
  });
  it("到期时刻缺失返回 null(非包周期实例)", () => {
    expect(formatExpiry(null, tZh, now)).toBeNull();
    expect(formatExpiry(undefined, tZh, now)).toBeNull();
  });
});

describe("formatDate", () => {
  it("只到日,空值仍为占位符", () => {
    expect(formatDate("2026-09-03T04:00:00Z")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(formatDate(null)).toBe("-");
  });
});
