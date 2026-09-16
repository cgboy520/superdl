/** Service route q/user_id/released allow-lists and URL round trip. */
import { describe, expect, it } from "vitest";

import { servicesValidateSearch } from "./services";

describe("services validateSearch", () => {
  it("valid values kept as-is, released accepts only 1", () => {
    expect(servicesValidateSearch({ q: "svc-ab", user_id: "7", released: "1" })).toEqual({
      q: "svc-ab",
      user_id: 7,
      released: "1",
    });
  });

  it("blank q, non-positive-integer user_id, other released values and unknown parameters are all stripped", () => {
    expect(servicesValidateSearch({ q: "  ", user_id: "0", released: "yes", foo: "bar" })).toEqual({});
    expect(servicesValidateSearch({ user_id: "abc" })).toEqual({});
  });
});
