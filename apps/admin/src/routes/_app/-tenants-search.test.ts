/** URL round trip of the tenant route tabs, search and instance filters. */
import { describe, expect, it } from "vitest";

import { Route } from "./tenants";

const validate = Route.options.validateSearch as (search: Record<string, unknown>) => Record<string, unknown>;

describe("tenants validateSearch", () => {
  it("tab/q serialisation round trip: valid values are kept as-is", () => {
    const out = validate({ tab: "instances", q: "13800001111", dtab: "billing" });
    expect(out).toEqual({ tab: "instances", q: "13800001111", dtab: "billing" });
    expect(validate({ dtab: "services" })).toEqual({ dtab: "services" });
    expect(validate({ dtab: "events" })).toEqual({ dtab: "events" });
  });

  it("invalid / blank values stripped: tabs outside the allow-list, blank q and unknown parameters never land", () => {
    const out = validate({ tab: "hacked", q: "", dtab: "nope", foo: "bar" });
    expect(out).toEqual({});
  });

  it("instance filters istatus/inode: istatus follows the instance status allow-list, inode is accepted when non-empty", () => {
    expect(validate({ istatus: "running", inode: "gpu-a3-01" })).toEqual({
      istatus: "running",
      inode: "gpu-a3-01",
    });
    expect(validate({ istatus: "bogus" })).toEqual({});
  });

  it("deletion request filter dstatus: follows the deletion status allow-list", () => {
    expect(validate({ dstatus: "pending" })).toEqual({ dstatus: "pending" });
    expect(validate({ dstatus: "bogus" })).toEqual({});
  });
});
