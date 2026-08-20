import { describe, expect, it } from "vitest";

import {
  addAmounts,
  compareAmounts,
  diskDailyEstimate,
  formatCountdown,
  formatDaysLeft,
  formatDuration,
  formatHourlyPrice,
  formatMoney,
  formatSizeGb,
  mulPrice,
} from "./format";

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

describe("formatDaysLeft", () => {
  const now = new Date("2026-08-19T12:00:00Z");
  it("宽限期剩余天数", () => {
    expect(formatDaysLeft("2026-08-17T00:00:00Z", 7, now)).toBe("剩 4 天");
  });
  it("最后一天显示今日到期", () => {
    expect(formatDaysLeft("2026-08-12T20:00:00Z", 7, now)).toBe("今日到期");
  });
  it("已越过截止", () => {
    expect(formatDaysLeft("2026-08-01T00:00:00Z", 7, now)).toBe("已到期");
  });
  it("起点缺失返回 null", () => {
    expect(formatDaysLeft(null, 7, now)).toBeNull();
    expect(formatDaysLeft(undefined, 30, now)).toBeNull();
  });
});

describe("formatMoney", () => {
  it("千分位与两位小数", () => {
    expect(formatMoney("1234.5")).toBe("¥1,234.50");
    expect(formatMoney("0")).toBe("¥0.00");
    expect(formatMoney("1000000")).toBe("¥1,000,000.00");
  });
  it("负数", () => {
    expect(formatMoney("-12.3")).toBe("-¥12.30");
  });
  it("空值兜底", () => {
    expect(formatMoney(null)).toBe("¥0.00");
    expect(formatMoney(undefined)).toBe("¥0.00");
  });
  it("截断而非四舍五入(展示层不做算术)", () => {
    expect(formatMoney("1.999")).toBe("¥1.99");
  });
});

describe("formatHourlyPrice", () => {
  it("保留有效小数,至少两位", () => {
    expect(formatHourlyPrice("1.6800")).toBe("¥1.68/时");
    expect(formatHourlyPrice("0.1250")).toBe("¥0.125/时");
    expect(formatHourlyPrice("3")).toBe("¥3.00/时");
  });
});

describe("formatDuration", () => {
  it("小时+分钟", () => {
    expect(formatDuration(3660)).toBe("1 小时 1 分");
    expect(formatDuration(7200)).toBe("2 小时");
    expect(formatDuration(120)).toBe("2 分钟");
    expect(formatDuration(30)).toBe("不足 1 分钟");
    expect(formatDuration(0)).toBe("0 分钟");
  });
});

describe("formatCountdown", () => {
  const now = new Date("2026-08-19T00:00:00Z");
  it("小时级", () => {
    expect(formatCountdown(new Date("2026-08-20T23:00:00Z"), now)).toBe("剩 47h");
  });
  it("分钟级", () => {
    expect(formatCountdown(new Date("2026-08-19T00:30:00Z"), now)).toBe("剩 30m");
  });
  it("已到期", () => {
    expect(formatCountdown(new Date("2026-08-18T00:00:00Z"), now)).toBe("已到期");
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
