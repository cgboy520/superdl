/** Reason validation: required, 2–200 characters after trim. */
import { describe, expect, it } from "vitest";

import { isValidReason } from "./validators";

describe("isValidReason (adjustment rejection reason is required)", () => {
  it("empty, whitespace-only and single-character values fail", () => {
    expect(isValidReason(undefined)).toBe(false);
    expect(isValidReason(null)).toBe(false);
    expect(isValidReason("")).toBe(false);
    expect(isValidReason("   ")).toBe(false);
    expect(isValidReason("x")).toBe(false);
    expect(isValidReason(" x ")).toBe(false);
  });

  it("two or more characters pass (surrounding whitespace not counted)", () => {
    expect(isValidReason("ok")).toBe(true);
    expect(isValidReason(" amount entered wrong ")).toBe(true);
  });

  it("more than 200 characters fails", () => {
    expect(isValidReason("a".repeat(200))).toBe(true);
    expect(isValidReason("a".repeat(201))).toBe(false);
  });
});
