/** URL parsing of the finance route reconciliation day, tab and settlement gap filters. */
import { describe, expect, it } from "vitest";

import { Route } from "./finance";

const validate = Route.options.validateSearch as (search: Record<string, unknown>) => Record<string, unknown>;

describe("finance validateSearch (settlement gaps)", () => {
  it("g_kind follows the kind allow-list, g_open accepts only 0 (the unresolved-only default stays out of the URL)", () => {
    expect(validate({ tab: "gaps", g_kind: "daily_disk", g_open: "0" })).toEqual({
      tab: "gaps",
      g_kind: "daily_disk",
      g_open: "0",
    });
    expect(validate({ g_kind: "weekly", g_open: "1" })).toEqual({});
  });

  it("the reconciliation day accepts only YYYY-MM-DD", () => {
    expect(validate({ day: "2026-09-13" })).toEqual({ day: "2026-09-13" });
    expect(validate({ day: "2026-9-13" })).toEqual({});
  });

  it("audit is its own page: ?tab=audit is no longer a valid tab (falls back to orders)", () => {
    expect(validate({ tab: "audit" })).toEqual({});
    expect(validate({ tab: "anomalies" })).toEqual({ tab: "anomalies" });
  });
});
