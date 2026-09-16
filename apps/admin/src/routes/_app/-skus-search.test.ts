/** URL round trip of the SKU route model, tier, on-sale and name filters. */
import { describe, expect, it } from "vitest";

import { skusValidateSearch } from "./skus";

describe("skus validateSearch", () => {
  it("valid values kept as-is: any model string, tiers from skuTierMap keys, sale only on/off", () => {
    expect(skusValidateSearch({ model: "RTX4090", tier: "shared_hami", sale: "off", q: "4090" })).toEqual({
      model: "RTX4090",
      tier: "shared_hami",
      sale: "off",
      q: "4090",
    });
    expect(skusValidateSearch({ sale: "on" })).toEqual({ sale: "on" });
  });

  it("invalid and blank values stripped: tiers outside the allow-list, other sale values, empty strings and unknown parameters never land", () => {
    expect(skusValidateSearch({ tier: "bogus", sale: "all", model: "  ", q: "", foo: "bar" })).toEqual({});
  });
});
