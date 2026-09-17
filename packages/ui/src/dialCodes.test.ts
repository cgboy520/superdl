import { describe, expect, it } from "vitest";

import { composeE164, DIAL_CODES, splitE164 } from "./dialCodes";

describe("dial codes", () => {
  it("compose and split round-trip through the longest matching dial code", () => {
    expect(composeE164("86", "138 0000 1111")).toBe("+8613800001111");
    expect(splitE164("+8613800001111")).toEqual({ dial: "86", national: "13800001111" });
    expect(splitE164("+85212345678")).toEqual({ dial: "852", national: "12345678" });
    expect(composeE164("1", "")).toBeNull();
    expect(composeE164("86", "0")).toBeNull();
    expect(splitE164("13800001111")).toBeNull();
  });

  it("list has unique dial codes", () => {
    expect(new Set(DIAL_CODES.map((d) => d.dial)).size).toBe(DIAL_CODES.length);
  });
});
