/** 手输原因校验:必填、trim 后 2~200 字。挂了 = 空理由进审计。 */
import { describe, expect, it } from "vitest";

import { isValidReason } from "./validators";

describe("isValidReason(调账驳回理由必填)", () => {
  it("空值/纯空白/单字均不通过", () => {
    expect(isValidReason(undefined)).toBe(false);
    expect(isValidReason(null)).toBe(false);
    expect(isValidReason("")).toBe(false);
    expect(isValidReason("   ")).toBe(false);
    expect(isValidReason("错")).toBe(false);
    expect(isValidReason(" 错 ")).toBe(false);
  });

  it("两字及以上通过(首尾空白不计)", () => {
    expect(isValidReason("误调")).toBe(true);
    expect(isValidReason(" 金额录错了 ")).toBe(true);
  });

  it("超过 200 字不通过", () => {
    expect(isValidReason("很".repeat(200))).toBe(true);
    expect(isValidReason("很".repeat(201))).toBe(false);
  });
});
