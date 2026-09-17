/** Node route deep link and q/pool/status filter allow-lists. */
import { describe, expect, it } from "vitest";

import { Route } from "./nodes";

const validate = Route.options.validateSearch as (search: Record<string, unknown>) => Record<string, unknown>;

describe("nodes validateSearch", () => {
  it("node/q/pool accepted when non-empty, status follows the node status allow-list", () => {
    expect(validate({ node: "gpu-a3-01", q: "gpu", pool: "hami", status: "Cordoned" })).toEqual({
      node: "gpu-a3-01",
      q: "gpu",
      pool: "hami",
      status: "Cordoned",
    });
  });

  it("blanks and invalid statuses stripped, unknown parameters never land", () => {
    expect(validate({ node: "", q: "  ", pool: "", status: "Broken", foo: "bar" })).toEqual({});
  });
});
