/** Overview route alert severity allow-list. */
import { describe, expect, it } from "vitest";

import { Route } from "./index";

const validate = Route.options.validateSearch as (search: Record<string, unknown>) => Record<string, unknown>;

describe("overview validateSearch", () => {
  it("the three valid severity levels are kept as-is", () => {
    expect(validate({ severity: "critical" })).toEqual({ severity: "critical" });
    expect(validate({ severity: "warning" })).toEqual({ severity: "warning" });
    expect(validate({ severity: "info" })).toEqual({ severity: "info" });
  });

  it("levels outside the allow-list, blanks and unknown parameters never land", () => {
    expect(validate({ severity: "fatal", foo: "bar" })).toEqual({});
    expect(validate({ severity: "" })).toEqual({});
    expect(validate({})).toEqual({});
  });
});
