import { createInstance } from "i18next";
import { describe, expect, it } from "vitest";

import enShared from "../locales/en-US/shared.json";
import zhShared from "../locales/zh-CN/shared.json";
import {
  addAmounts,
  compareAmounts,
  diskDailyEstimate,
  formatCountdown,
  formatDaysLeft,
  formatDuration,
  formatHourlyPrice,
  formatMoney,
  formatReclaimCountdown,
  formatSizeGb,
  makeFormatters,
  mulPrice,
  type SharedT,
} from "./format";

/** 真实 i18next 实例渲染 shared.json,量词键与复数边界一并被本测试锁死。 */
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
  return ((key: string, opts?: Record<string, unknown>) => inst.t(key, opts)) as SharedT;
}
const tZh = makeT("zh-CN");
const tEn = makeT("en-US");

describe("addAmounts", () => {
  it("BigInt 精确相加无浮点误差", () => {
    expect(addAmounts("1.10", "2.80")).toBe("3.90");
    expect(addAmounts("0.01", "0.02")).toBe("0.03");
  });
  it("负数与空值", () => {
    expect(addAmounts("-1.50", "1.00")).toBe("-0.50");
    expect(addAmounts(null, "2.00")).toBe("2.00");
    expect(addAmounts(null, undefined)).toBe("0.00");
  });
});

describe("compareAmounts", () => {
  it("BigInt 精确比较无浮点误差", () => {
    expect(compareAmounts("0.30", "0.3000")).toBe(0);
    expect(compareAmounts("100.00", "99.9999")).toBe(1);
    expect(compareAmounts("1.6799", "1.68")).toBe(-1);
    // 0.1+0.2 场景:字符串比较仍然精确
    expect(compareAmounts("0.3000", "0.2999")).toBe(1);
  });
  it("负数与空值", () => {
    expect(compareAmounts("-0.01", "0")).toBe(-1);
    expect(compareAmounts(null, "0.00")).toBe(0);
    expect(compareAmounts(undefined, "-1")).toBe(1);
  });
});

describe("mulPrice", () => {
  it("BigInt 精确乘法无浮点误差", () => {
    expect(mulPrice("1.9900", 2)).toBe("3.9800");
    expect(mulPrice("0.98", 3)).toBe("2.9400");
    expect(mulPrice("0.0001", 8)).toBe("0.0008");
  });
  it("空值兜底", () => {
    expect(mulPrice(null, 4)).toBe("0.0000");
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
  it("起点缺失返回 null", () => {
    expect(formatDaysLeft(null, 7, t, now)).toBeNull();
    expect(formatDaysLeft(undefined, 30, t, now)).toBeNull();
  });
});

describe("formatMoney", () => {
  it("zh:千分位与两位小数", () => {
    expect(formatMoney("1234.5", "zh-CN")).toBe("¥1,234.50");
    expect(formatMoney("0", "zh-CN")).toBe("¥0.00");
    expect(formatMoney("1000000", "zh-CN")).toBe("¥1,000,000.00");
  });
  it("en:CN¥ 符号避免日元歧义", () => {
    expect(formatMoney("1234.5", "en-US")).toBe("CN¥1,234.50");
    expect(formatMoney("-12.3", "en-US")).toBe("-CN¥12.30");
  });
  it("负数", () => {
    expect(formatMoney("-12.3", "zh-CN")).toBe("-¥12.30");
  });
  it("空值兜底", () => {
    expect(formatMoney(null, "zh-CN")).toBe("¥0.00");
    expect(formatMoney(undefined, "en-US")).toBe("CN¥0.00");
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
    expect(formatHourlyPrice(null, tZh, "zh-CN")).toBe("¥0.00/时");
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
    expect(formatReclaimCountdown(new Date("2026-08-18T00:00:00Z"), t, now)).toBe(
      zh ? "即将回收" : "reclaiming soon",
    );
  });
});

describe("makeFormatters", () => {
  it("绑定 locale 后调用点保持原形", () => {
    const fmt = makeFormatters(tZh, "zh-CN");
    expect(fmt.formatMoney("12.3")).toBe("¥12.30");
    expect(fmt.formatHourlyPrice("1.68")).toBe("¥1.68/时");
    expect(fmt.formatDuration(120)).toBe("2 分钟");
  });
});

describe("formatSizeGb", () => {
  it("GB 与 TB", () => {
    expect(formatSizeGb(100)).toBe("100 GB");
    expect(formatSizeGb(1024)).toBe("1 TB");
    expect(formatSizeGb(1536)).toBe("1.5 TB");
  });
});

describe("diskDailyEstimate", () => {
  it("月价折日价 HALF_UP 到分", () => {
    expect(diskDailyEstimate("0.5000", 100)).toBe("1.67"); // 50/30=1.666…→1.67
    expect(diskDailyEstimate("0.5000", 60)).toBe("1.00"); // 30/30=1.00
    expect(diskDailyEstimate("0.1000", 10)).toBe("0.03"); // 1/30=0.0333→0.03
    expect(diskDailyEstimate("1.2345", 30)).toBe("1.23"); // 37.035/30=1.2345→1.23
  });
  it("边界:空价/0GB/非整数 GB 返回 0.00", () => {
    expect(diskDailyEstimate(null, 100)).toBe("0.00");
    expect(diskDailyEstimate("", 100)).toBe("0.00");
    expect(diskDailyEstimate("0.50", 0)).toBe("0.00");
    expect(diskDailyEstimate("0.50", 1.5)).toBe("0.00");
  });
  it("不产生浮点误差(0.1+0.2 类场景)", () => {
    expect(diskDailyEstimate("0.3000", 1000)).toBe("10.00"); // 300/30
  });
});
