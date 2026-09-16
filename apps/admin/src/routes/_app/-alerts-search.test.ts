/** Alert route severity/acked/type allow-lists and URL round trip. */
import { describe, expect, it } from "vitest";

import { Route } from "./alerts";

const validate = Route.options.validateSearch as (search: Record<string, unknown>) => Record<string, unknown>;

describe("alerts validateSearch", () => {
  it("valid severity/acked/type values are kept as-is", () => {
    expect(validate({ severity: "critical", acked: "unacked", type: "gpu_fault" })).toEqual({
      severity: "critical",
      acked: "unacked",
      type: "gpu_fault",
    });
    expect(validate({ severity: "info", acked: "acked", type: "admin_alert" })).toEqual({
      severity: "info",
      acked: "acked",
      type: "admin_alert",
    });
  });

  it("invalid / blank values stripped: levels, states and types outside the allow-list and unknown parameters never land", () => {
    expect(validate({ severity: "fatal", acked: "maybe", type: "whatever", foo: "bar" })).toEqual({});
    expect(validate({ severity: "", acked: "", type: "" })).toEqual({});
  });
});
