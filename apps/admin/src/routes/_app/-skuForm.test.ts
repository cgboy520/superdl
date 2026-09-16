/** SKU recommended values, CPU field zeroing and create/edit submit payloads. */
import { describe, expect, it } from "vitest";

import type { GpuModelAggregate, SkuAdminOut } from "../../api";
import { buildSkuPayload, recommendFields, type SkuFormValues } from "./-skuForm";

const AGG: GpuModelAggregate = {
  gpu_model: "RTX 4090",
  gpu_model_raw: "NVIDIA GeForce RTX 4090",
  gpu_total: 8,
  mem_gb_per_gpu: 64,
  node_count: 2,
  pool_label: "hami",
  ready_gpu_free: 5,
  ready_gpu_total: 6,
  vcpu_per_gpu: 16,
  vram_gb: 48,
};

describe("recommendFields", () => {
  it("HAMi tier scales vram/vcpu/mem by share (rounded, floor 1), pct goes into the form as-is", () => {
    expect(recommendFields(AGG, "shared_hami", 50)).toEqual({
      gpu_model: "RTX 4090",
      pool_label: "hami",
      gpu_cores_pct: 50,
      vram_gb: 24,
      vcpu: 8,
      mem_gb: 32,
    });
  });

  it("whole card and MIG do not scale: pct pinned to 100, the spec takes a full share", () => {
    const fields = recommendFields(AGG, "dedicated", 50);
    expect(fields.gpu_cores_pct).toBe(100);
    expect(fields.vram_gb).toBe(48);
    expect(fields.vcpu).toBe(16);
  });

  it("agg without vcpu_per_gpu / mem_gb_per_gpu leaves those two fields untouched (form values kept)", () => {
    const fields = recommendFields({ ...AGG, vcpu_per_gpu: 0, mem_gb_per_gpu: 0 }, "shared_hami", 25);
    expect(fields).not.toHaveProperty("vcpu");
    expect(fields).not.toHaveProperty("mem_gb");
  });
});

const BASE_VALUES: SkuFormValues = {
  name: "test-sku",
  gpu_model: "RTX 4090",
  variant: "shared_hami",
  gpu_cores_pct: 50,
  vram_gb: 24,
  oversell_cores: 1.5,
  pool_label: "hami",
  vcpu: 8,
  mem_gb: 32,
  disk_gb: 100,
  price_hourly: "1.9900",
  max_gpus_per_instance: 4,
  period_enabled: true,
  spot_enabled: false,
};

describe("buildSkuPayload", () => {
  it("create: tier derived from the display tier, no reason", () => {
    const p = buildSkuPayload(BASE_VALUES, "new");
    expect(p).not.toBeNull();
    if (p?.kind !== "create") throw new Error("expected create");
    expect(p.data.tier).toBe("shared");
    expect(p.data.pool_label).toBe("hami");
    expect(p.data.gpu_model).toBe("RTX 4090");
    expect(p.data.oversell_cores).toBe("1.5");
    expect("reason" in p.data).toBe(false);
  });

  it("CPU tier: GPU fields zeroed, tier=cpu, pool taken from the form", () => {
    const p = buildSkuPayload({ ...BASE_VALUES, variant: "cpu", pool_label: "cpu" }, "new");
    if (p?.kind !== "create") throw new Error("expected create");
    expect(p.data.tier).toBe("cpu");
    expect(p.data.gpu_model).toBe("");
    expect(p.data.gpu_cores_pct).toBe(0);
    expect(p.data.vram_gb).toBe(0);
    expect(p.data.max_gpus_per_instance).toBe(0);
    expect(p.data.pool_label).toBe("cpu");
  });

  it("edit: no gpu_model (the model cannot change), carries reason; editing=null returns null", () => {
    const record = { id: 7 } as SkuAdminOut;
    const p = buildSkuPayload({ ...BASE_VALUES, reason: "reprice" }, record);
    if (p?.kind !== "update") throw new Error("expected update");
    expect(p.skuId).toBe(7);
    expect("gpu_model" in p.data).toBe(false);
    expect(p.data.reason).toBe("reprice");
    expect(buildSkuPayload(BASE_VALUES, null)).toBeNull();
  });
});
