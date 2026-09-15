/** SKU 推荐值、CPU 字段补零与新建/编辑提交负载测试。 */
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
  it("HAMi 档按份额折算 vram/vcpu/mem(取整,下限 1),pct 原样入表", () => {
    expect(recommendFields(AGG, "shared_hami", 50)).toEqual({
      gpu_model: "RTX 4090",
      pool_label: "hami",
      gpu_cores_pct: 50,
      vram_gb: 24,
      vcpu: 8,
      mem_gb: 32,
    });
  });

  it("整卡与 MIG 不折:pct 钉 100,规格拿整份", () => {
    const fields = recommendFields(AGG, "dedicated", 50);
    expect(fields.gpu_cores_pct).toBe(100);
    expect(fields.vram_gb).toBe(48);
    expect(fields.vcpu).toBe(16);
  });

  it("agg 缺 vcpu_per_gpu / mem_gb_per_gpu 时不写这两个字段(留表单现值)", () => {
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
  it("新建:tier 由档位派生,不带 reason", () => {
    const p = buildSkuPayload(BASE_VALUES, "new");
    expect(p).not.toBeNull();
    if (p?.kind !== "create") throw new Error("expected create");
    expect(p.data.tier).toBe("shared");
    expect(p.data.pool_label).toBe("hami");
    expect(p.data.gpu_model).toBe("RTX 4090");
    expect(p.data.oversell_cores).toBe("1.5");
    expect("reason" in p.data).toBe(false);
  });

  it("CPU 档:GPU 字段补零,tier=cpu,池取表单可选值", () => {
    const p = buildSkuPayload({ ...BASE_VALUES, variant: "cpu", pool_label: "cpu" }, "new");
    if (p?.kind !== "create") throw new Error("expected create");
    expect(p.data.tier).toBe("cpu");
    expect(p.data.gpu_model).toBe("");
    expect(p.data.gpu_cores_pct).toBe(0);
    expect(p.data.vram_gb).toBe(0);
    expect(p.data.max_gpus_per_instance).toBe(0);
    expect(p.data.pool_label).toBe("cpu");
  });

  it("编辑:不带 gpu_model(型号不可改),带 reason;editing=null 返回 null", () => {
    const record = { id: 7 } as SkuAdminOut;
    const p = buildSkuPayload({ ...BASE_VALUES, reason: "调价" }, record);
    if (p?.kind !== "update") throw new Error("expected update");
    expect(p.skuId).toBe(7);
    expect("gpu_model" in p.data).toBe(false);
    expect(p.data.reason).toBe("调价");
    expect(buildSkuPayload(BASE_VALUES, null)).toBeNull();
  });
});
