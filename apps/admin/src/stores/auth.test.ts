/** Invoice read permission is finance/admin only. */
import { describe, expect, it } from "vitest";

import { canReadInvoices } from "./auth";

describe("canReadInvoices", () => {
  it("only finance and admin can read invoices (ops/readonly never)", () => {
    expect(canReadInvoices("finance")).toBe(true);
    expect(canReadInvoices("admin")).toBe(true);
    expect(canReadInvoices("ops")).toBe(false);
    expect(canReadInvoices("readonly")).toBe(false);
  });
});
