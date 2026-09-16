/** URL round trip of the market page filters, selection and purchase parameters. */
import { describe, expect, it } from "vitest";

import { marketValidateSearch } from "./_console.market";

describe("market validateSearch", () => {
  it("full round trip of the 10 parameters: valid values are all kept (numeric strings normalised to numbers)", () => {
    const input = {
      kind: "cpu",
      mode: "month",
      model: "H100",
      tier: "dedicated",
      vram: "80",
      qty: "4",
      vcpu: "16",
      mem: "128",
      sku: "42",
      count: "3",
    };
    expect(marketValidateSearch(input)).toEqual({
      kind: "cpu",
      mode: "month",
      model: "H100",
      tier: "dedicated",
      vram: 80,
      qty: 4,
      vcpu: 16,
      mem: 128,
      sku: 42,
      count: 3,
    });
  });

  it("defaults stripped: kind=gpu / mode=on_demand / qty=1 / count=1 / empty tiers and 0 values stay out of the URL", () => {
    expect(
      marketValidateSearch({
        kind: "gpu",
        mode: "on_demand",
        model: "",
        tier: "",
        vram: 0,
        qty: 1,
        vcpu: 0,
        mem: 0,
        count: 1,
      }),
    ).toEqual({});
  });

  it("invalid values dropped: unknown kind/mode/tier, negatives, floats, over-cap durations and non-numbers fall back to defaults", () => {
    expect(
      marketValidateSearch({
        kind: "tpu",
        mode: "hourly",
        tier: "cpu",
        vram: -1,
        qty: 2.5,
        vcpu: "abc",
        mem: null,
        sku: 0,
        count: 99,
      }),
    ).toEqual({});
  });

  it("spot and period tiers recognised: mode=spot kept, display tier (shared_mig) kept", () => {
    expect(marketValidateSearch({ mode: "spot", tier: "shared_mig" })).toEqual({
      mode: "spot",
      tier: "shared_mig",
    });
  });

  it("URL round-trip serialisation: validating the validate output is idempotent (controls bound to search do not drift)", () => {
    const once = marketValidateSearch({
      kind: "cpu",
      mode: "week",
      vcpu: "8",
      mem: "64",
      sku: "7",
    });
    expect(marketValidateSearch(once as Record<string, unknown>)).toEqual(once);
  });
});
