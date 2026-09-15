/** 发票读取权限只允许 finance/admin。 */
import { describe, expect, it } from "vitest";

import { canReadInvoices } from "./auth";

describe("canReadInvoices", () => {
  it("只有 finance 与 admin 能读发票(ops/readonly 一律不给)", () => {
    expect(canReadInvoices("finance")).toBe(true);
    expect(canReadInvoices("admin")).toBe(true);
    expect(canReadInvoices("ops")).toBe(false);
    expect(canReadInvoices("readonly")).toBe(false);
  });
});
