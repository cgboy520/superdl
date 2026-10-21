/** Market stock aggregation: deduplicated per physical pool, labelled with the display model. */
import { describe, expect, it } from "vitest";

import { dedupAvailableByModel, dedupAvailableTotal, type SkuAvailabilityLike } from "./inventory";

const sku = (over: Partial<SkuAvailabilityLike>): SkuAvailabilityLike => ({
  pool_label: "hami",
  gpu_model: "CMP170HX",
  display_model: "CMP170HX",
  available_count: 0,
  ...over,
});

describe("dedupAvailableByModel", () => {
  it("SKUs on the same physical pool count once and the chip carries the display model", () => {
    const skus = [
      sku({ display_model: "GA100-PCIE-64GB", available_count: 40 }),
      sku({ display_model: "GA100-PCIE-64GB", available_count: 16 }),
      sku({ gpu_model: "", display_model: "", pool_label: "cpu", available_count: 99 }),
    ];
    expect([...dedupAvailableByModel(skus)]).toEqual([["GA100-PCIE-64GB", 40]]);
    expect(dedupAvailableTotal(skus)).toBe(40);
  });
});
