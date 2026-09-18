import { createInstance } from "i18next";
import { describe, expect, it } from "vitest";

import enShared from "../locales/en-US/shared.json";
import zhShared from "../locales/zh-CN/shared.json";
import {
  addAmounts,
  compareAmounts,
  currencySymbol,
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
  minorUnitsOf,
  mulPrice,
  quoteSubscription,
  spotHourlyPrice,
  type SharedT,
} from "./format";

/** Real i18next instance rendering shared.json. */
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
  it("adds BigInt exactly without float error", () => {
    expect(addAmounts("1.10", "2.80")).toBe("3.90");
    expect(addAmounts("0.01", "0.02")).toBe("0.03");
  });
  it("negative numbers", () => {
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
  it("compares BigInt exactly without float error", () => {
    expect(compareAmounts("0.30", "0.3000")).toBe(0);
    expect(compareAmounts("100.00", "99.9999")).toBe(1);
    expect(compareAmounts("1.6799", "1.68")).toBe(-1);
    expect(compareAmounts("0.3000", "0.2999")).toBe(1);
  });
  it("negative numbers", () => {
    expect(compareAmounts("-0.01", "0")).toBe(-1);
  });
});

describe("mulPrice", () => {
  it("multiplies BigInt exactly without float error", () => {
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
  it("grace days left (en plural)", () => {
    expect(formatDaysLeft("2026-08-17T00:00:00Z", 7, t, now)).toBe(zh ? "剩 4 天" : "4 days left"); // cjk-ok
  });
  it("1 day left (en singular boundary)", () => {
    expect(formatDaysLeft("2026-08-14T00:00:00Z", 7, t, now)).toBe(zh ? "剩 1 天" : "1 day left"); // cjk-ok
  });
  it("shows due today on the last day", () => {
    expect(formatDaysLeft("2026-08-12T20:00:00Z", 7, t, now)).toBe(zh ? "今日到期" : "Due today"); // cjk-ok
  });
  it("past the deadline", () => {
    expect(formatDaysLeft("2026-08-01T00:00:00Z", 7, t, now)).toBe(zh ? "已到期" : "Expired"); // cjk-ok
  });
});

it("formatDaysLeft returns null without a start (language-independent)", () => {
  expect(formatDaysLeft(null, 7, tZh)).toBeNull();
  expect(formatDaysLeft(undefined, 30, tZh)).toBeNull();
});

describe("formatMoney", () => {
  it("CNY: zh-CN uses ¥, en-US uses CN¥ (no yen ambiguity), grouping and two decimals", () => {
    expect(formatMoney("1234.5", "zh-CN", "CNY")).toBe("¥1,234.50");
    expect(formatMoney("0", "zh-CN", "CNY")).toBe("¥0.00");
    expect(formatMoney("1234.5", "en-US", "CNY")).toBe("CN¥1,234.50");
    expect(formatMoney("-12.3", "en-US", "CNY")).toBe("-CN¥12.30");
  });
  it("USD and JPY follow Intl: $ with cents, ¥ with whole units", () => {
    expect(formatMoney("1234.5", "en-US", "USD")).toBe("$1,234.50");
    expect(formatMoney("1234.5", "zh-CN", "USD")).toBe("US$1,234.50");
    expect(formatMoney("1235", "en-US", "JPY")).toBe("¥1,235");
    expect(formatMoney("1235.9", "ja-JP", "JPY")).toBe("￥1,235"); // cjk-ok
  });
  it("truncates instead of rounding (the display layer does no arithmetic)", () => {
    expect(formatMoney("1.999", "zh-CN", "CNY")).toBe("¥1.99");
    expect(formatMoney("1.999", "en-US", "JPY")).toBe("¥1");
  });
  it("unknown or unset currency renders a plain number without a symbol", () => {
    expect(formatMoney("1234.5", "en-US", null)).toBe("1,234.50");
    expect(formatMoney("1234.5", "en-US", "NOPE")).toBe("1,234.50");
    expect(currencySymbol("en-US", null)).toBe("");
  });
  it("currencySymbol and minorUnitsOf agree with Intl", () => {
    expect(currencySymbol("zh-CN", "CNY")).toBe("¥");
    expect(currencySymbol("en-US", "CNY")).toBe("CN¥");
    expect(currencySymbol("en-US", "USD")).toBe("$");
    expect(minorUnitsOf("JPY")).toBe(0);
    expect(minorUnitsOf("USD")).toBe(2);
    expect(minorUnitsOf(null)).toBe(2);
  });
});

describe("formatHourlyPrice", () => {
  it("zh: keeps significant decimals, at least the minor units", () => {
    expect(formatHourlyPrice("1.6800", tZh, "zh-CN", "CNY")).toBe("¥1.68/时"); // cjk-ok
    expect(formatHourlyPrice("0.1250", tZh, "zh-CN", "CNY")).toBe("¥0.125/时"); // cjk-ok
    expect(formatHourlyPrice("3", tZh, "zh-CN", "CNY")).toBe("¥3.00/时"); // cjk-ok
  });
  it("en: /hr unit, symbol from the currency", () => {
    expect(formatHourlyPrice("1.6800", tEn, "en-US", "CNY")).toBe("CN¥1.68/hr");
    expect(formatHourlyPrice("1.6800", tEn, "en-US", "USD")).toBe("$1.68/hr");
    expect(formatHourlyPrice("0.5", tEn, "en-US", "JPY")).toBe("¥0.5/hr");
  });
});

describe.each([
  ["zh-CN", tZh],
  ["en-US", tEn],
] as const)("formatDuration (%s)", (lng, t) => {
  const zh = lng === "zh-CN";
  it("hours + minutes", () => {
    expect(formatDuration(3660, t)).toBe(zh ? "1 小时 1 分" : "1 hr 1 min"); // cjk-ok
    expect(formatDuration(7200, t)).toBe(zh ? "2 小时" : "2 hr"); // cjk-ok
    expect(formatDuration(120, t)).toBe(zh ? "2 分钟" : "2 min"); // cjk-ok
    expect(formatDuration(30, t)).toBe(zh ? "不足 1 分钟" : "less than a minute"); // cjk-ok
    expect(formatDuration(0, t)).toBe(zh ? "0 分钟" : "0 min"); // cjk-ok
  });
});

describe.each([
  ["zh-CN", tZh],
  ["en-US", tEn],
] as const)("formatCountdown (%s)", (lng, t) => {
  const now = new Date("2026-08-19T00:00:00Z");
  const zh = lng === "zh-CN";
  it("hour level", () => {
    expect(formatCountdown(new Date("2026-08-20T23:00:00Z"), t, now)).toBe(zh ? "剩 47h" : "47h left"); // cjk-ok
  });
  it("minute level", () => {
    expect(formatCountdown(new Date("2026-08-19T00:30:00Z"), t, now)).toBe(zh ? "剩 30m" : "30m left"); // cjk-ok
  });
  it("expired", () => {
    expect(formatCountdown(new Date("2026-08-18T00:00:00Z"), t, now)).toBe(zh ? "已到期" : "Expired"); // cjk-ok
  });
  it("frozen inline whole sentence (the zh zero-morpheme concatenation is its own key)", () => {
    expect(formatReclaimCountdown(new Date("2026-08-20T23:00:00Z"), t, now)).toBe(
      zh ? "剩 47h后回收" : "reclaimed in 47h", // cjk-ok
    );
    expect(formatReclaimCountdown(new Date("2026-08-19T00:30:00Z"), t, now)).toBe(
      zh ? "剩 30m后回收" : "reclaimed in 30m", // cjk-ok
    );
    expect(formatReclaimCountdown(new Date("2026-08-18T00:00:00Z"), t, now)).toBe(zh ? "即将回收" : "reclaiming soon"); // cjk-ok
  });
});

describe("formatSizeGb", () => {
  it("GB and TB", () => {
    expect(formatSizeGb(100)).toBe("100 GB");
    expect(formatSizeGb(1024)).toBe("1 TB");
    expect(formatSizeGb(1536)).toBe("1.5 TB");
  });
});

describe("formatDateTime time-zone suffix", () => {
  it("outputs a (UTC±x) suffix matching the runtime offset", () => {
    const out = formatDateTime("2026-08-19T02:30:00Z");
    expect(out).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2} \(UTC[+-]\d+(:\d{2})?\)$/);
    const d = new Date("2026-08-19T02:30:00Z");
    const offsetMin = -d.getTimezoneOffset();
    const sign = offsetMin >= 0 ? "+" : "-";
    const abs = Math.abs(offsetMin);
    const suffix = `(UTC${sign}${Math.floor(abs / 60)}${abs % 60 ? `:${String(abs % 60).padStart(2, "0")}` : ""})`;
    expect(out.endsWith(suffix)).toBe(true);
  });
  it("empty values stay the placeholder", () => {
    expect(formatDateTime(null)).toBe("-");
    expect(formatDateTime(undefined)).toBe("-");
  });
});

describe("diskDailyEstimate", () => {
  it("monthly price to daily price, HALF_EVEN to the cent", () => {
    expect(diskDailyEstimate("0.5000", 100, 2)).toBe("1.67");
    expect(diskDailyEstimate("0.5000", 60, 2)).toBe("1.00");
    expect(diskDailyEstimate("0.1000", 10, 2)).toBe("0.03");
    expect(diskDailyEstimate("1.2345", 30, 2)).toBe("1.23");
  });
  it("cent ties round to even, the same semantics as the backend as_amount", () => {
    expect(diskDailyEstimate("0.0350", 30, 2)).toBe("0.04");
    expect(diskDailyEstimate("0.0350", 90, 2)).toBe("0.10");
    expect(diskDailyEstimate("0.0500", 15, 2)).toBe("0.02");
  });
  it("edge cases: empty price / 0 GB / non-integer GB return 0.00", () => {
    expect(diskDailyEstimate("", 100, 2)).toBe("0.00");
    expect(diskDailyEstimate("0.50", 0, 2)).toBe("0.00");
    expect(diskDailyEstimate("0.50", 1.5, 2)).toBe("0.00");
  });
  it("zero-decimal currencies round to whole units (HALF_EVEN)", () => {
    expect(diskDailyEstimate("15.0000", 100, 0)).toBe("50");
    expect(diskDailyEstimate("0.4500", 100, 0)).toBe("2");
    expect(diskDailyEstimate("0.7500", 100, 0)).toBe("2");
    expect(diskDailyEstimate("", 100, 0)).toBe("0");
  });
});

describe("quoteSubscription", () => {
  it("quantises step by step like the backend quote_subscription (discounted hourly to 4 dp first, then × units × hours to the cent)", () => {
    const q = quoteSubscription("3.9900", { units: 1, period: "month", periodCount: 1, discountPct: 80 });
    expect(q.hours).toBe(720);
    expect(q.unitPrice).toBe("3.1920");
    expect(q.listAmount).toBe("2872.80");
    expect(q.discountAmount).toBe("574.56");
    expect(q.amount).toBe("2298.24");
  });
  it("the triple is consistent: discountAmount === listAmount - amount", () => {
    for (const pct of [95, 90, 80, 70]) {
      for (const period of ["day", "week", "month", "year"] as const) {
        const q = quoteSubscription("1.2345", { units: 3, period, periodCount: 2, discountPct: pct });
        expect(addAmounts(q.amount, q.discountAmount)).toBe(q.listAmount);
      }
    }
  });
  it("units and period count are multipliers: CPU instances (units=1) do not scale by card", () => {
    const one = quoteSubscription("2.0000", { units: 1, period: "day", periodCount: 1, discountPct: 95 });
    const four = quoteSubscription("2.0000", { units: 4, period: "day", periodCount: 1, discountPct: 95 });
    expect(one.amount).toBe("45.60");
    expect(four.amount).toBe("182.40");
  });
  it("yearly 8760 hours neither overflows nor loses precision", () => {
    const q = quoteSubscription("3.9900", { units: 8, period: "year", periodCount: 3, discountPct: 70 });
    expect(q.hours).toBe(26280);
    expect(q.unitPrice).toBe("2.7930");
    expect(q.listAmount).toBe("838857.60");
    expect(q.amount).toBe("587200.32");
  });
  it("discountPct 100 (full list price, no discount) stays consistent", () => {
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
  it("single period", () => {
    expect(formatPeriodPrice("2298.24", "month", 1, t, locale, "CNY")).toBe(zh ? "¥2,298.24/月" : "CN¥2,298.24/month"); // cjk-ok
  });
  it("several periods (en uses plural units)", () => {
    expect(formatPeriodPrice("6894.72", "month", 3, t, locale, "CNY")).toBe(
      zh ? "¥6,894.72/3 月" : "CN¥6,894.72 per 3 months", // cjk-ok
    );
  });
  it("an unknown period returns the amount only, no invented unit", () => {
    expect(formatPeriodPrice("10.00", "quarter", 1, t, locale, "CNY")).toBe(zh ? "¥10.00" : "CN¥10.00");
  });
});

describe("spotHourlyPrice", () => {
  it("matches the backend as_price (price × pct / 100): HALF_EVEN to 4 dp", () => {
    expect(spotHourlyPrice("3.9900", 40)).toBe("1.5960");
    expect(spotHourlyPrice("3.9900", 100)).toBe("3.9900");
  });
  it("exactly half a ten-thousandth rounds to even (the same rule as the quoteSubscription discounted hourly price)", () => {
    expect(spotHourlyPrice("0.0010", 45)).toBe("0.0004");
    expect(spotHourlyPrice("0.0030", 45)).toBe("0.0014");
    expect(spotHourlyPrice("0.0001", 45)).toBe("0.0000");
  });
  it("equals the quoteSubscription unitPrice value by value (one discount algorithm, never two)", () => {
    for (const pct of [10, 40, 45, 55, 90]) {
      const q = quoteSubscription("1.2345", { units: 1, period: "day", periodCount: 1, discountPct: pct });
      expect(spotHourlyPrice("1.2345", pct)).toBe(q.unitPrice);
    }
  });
});

describe("formatSpotDiscount", () => {
  const zh = makeT("zh-CN");
  const en = makeT("en-US");
  it("zh states the discount as a fraction of ten (40 → 4), whole tens without a decimal point", () => {
    // cjk-ok
    expect(formatSpotDiscount(40, zh, "zh-CN")).toBe("4 折"); // cjk-ok
    expect(formatSpotDiscount(90, zh, "zh-CN")).toBe("9 折"); // cjk-ok
  });
  it("zh keeps one decimal for non-tens (45 → 4.5)", () => {
    // cjk-ok
    expect(formatSpotDiscount(45, zh, "zh-CN")).toBe("4.5 折"); // cjk-ok
    expect(formatSpotDiscount(10, zh, "zh-CN")).toBe("1 折"); // cjk-ok
  });
  it("en states the percentage of the on-demand price (no English equivalent of the zh fraction wording)", () => {
    // cjk-ok
    expect(formatSpotDiscount(40, en, "en-US")).toBe("40% of on-demand");
    expect(formatSpotDiscount(45, en, "en-US")).toBe("45% of on-demand");
  });
});

describe("formatExpiry", () => {
  const now = new Date("2026-08-19T12:00:00Z");
  it("days left / due today / expired", () => {
    expect(formatExpiry("2026-09-11T12:00:00Z", tZh, now)).toBe("剩 23 天"); // cjk-ok
    expect(formatExpiry("2026-08-19T20:00:00Z", tZh, now)).toBe("今日到期"); // cjk-ok
    expect(formatExpiry("2026-08-18T12:00:00Z", tZh, now)).toBe("已到期"); // cjk-ok
  });
  it("returns null without an expiry instant (non-subscription instances)", () => {
    expect(formatExpiry(null, tZh, now)).toBeNull();
    expect(formatExpiry(undefined, tZh, now)).toBeNull();
  });
});

describe("formatDate", () => {
  it("date only, empty values stay the placeholder", () => {
    expect(formatDate("2026-09-03T04:00:00Z")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(formatDate(null)).toBe("-");
  });
});
