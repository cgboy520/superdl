import { describe, expect, it } from "vitest";

import {
  formatCountdown,
  formatDuration,
  formatHourlyPrice,
  formatMoney,
  formatSizeGb,
} from "./format";

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
