/** Pool switch target candidates and switchability (backend capability flags → target pools greyed; an observed card count of 0 can still switch back). */

import { describe, expect, it } from "vitest";

import { type NodeRow } from "../../api";
import { canSwitchPool, currentPool, inSwitchablePool, switchTargets } from "./-SwitchPoolModal";

/** Capability flags as the API computes them for the fixture models (core/gpu_models). */
const CAPS: Record<string, { supports_mig: boolean; supports_passthrough: boolean }> = {
  RTX4090: { supports_mig: false, supports_passthrough: true },
  GB10: { supports_mig: false, supports_passthrough: false },
  "H100-80G": { supports_mig: true, supports_passthrough: true },
};

function node(over: Partial<NodeRow> = {}): NodeRow {
  const gpu_model = over.gpu_model ?? "RTX4090";
  return {
    name: "n1",
    pool_label: "hami",
    gpu_model,
    gpu_total: 8,
    gpu_used: 0,
    status: "Ready",
    vcpu: 64,
    mem_gb: 256,
    disk_gb: 2048,
    ...CAPS[gpu_model],
    ...over,
  };
}

describe("switchTargets", () => {
  it("excludes the current pool; a model without MIG support greys mig instead of hiding it", () => {
    expect(switchTargets(node())).toEqual([
      { pool: "kata", disabled: false },
      { pool: "mig", disabled: true },
    ]);
  });

  it("integrated GPU: both other pools greyed, nothing left to switch to", () => {
    expect(switchTargets(node({ gpu_model: "GB10" }))).toEqual([
      { pool: "kata", disabled: true },
      { pool: "mig", disabled: true },
    ]);
  });

  it("the API flags are authoritative: contradictory flags override what the model name suggests", () => {
    expect(switchTargets(node({ gpu_model: "RTX4090", supports_mig: true, supports_passthrough: false }))).toEqual([
      { pool: "kata", disabled: true },
      { pool: "mig", disabled: false },
    ]);
    expect(switchTargets(node({ gpu_model: "GB10", supports_mig: true, supports_passthrough: true }))).toEqual([
      { pool: "kata", disabled: false },
      { pool: "mig", disabled: false },
    ]);
  });

  it("a MIG-capable model has two of three available", () => {
    expect(switchTargets(node({ gpu_model: "H100-80G", pool_label: "kata" }))).toEqual([
      { pool: "hami", disabled: false },
      { pool: "mig", disabled: false },
    ]);
  });

  it("mid-switch the current pool is the desired pool: the target itself is no longer a candidate", () => {
    const n = node({ pool_label: "hami", desired_pool: "kata", gpu_model: "H100-80G" });
    expect(currentPool(n)).toBe("kata");
    expect(switchTargets(n).map((o) => o.pool)).toEqual(["hami", "mig"]);
  });
});

describe("canSwitchPool", () => {
  it("the cpu pool and unlabelled nodes cannot switch", () => {
    expect(canSwitchPool(node({ pool_label: "cpu", gpu_total: 0 }))).toBe(false);
    expect(canSwitchPool(node({ pool_label: "", gpu_total: 0 }))).toBe(false);
  });

  it("observed card count dropped to 0 but still in a GPU pool: can still switch back, otherwise the node is locked in a bad pool", () => {
    expect(canSwitchPool(node({ pool_label: "kata", gpu_model: "GB10", gpu_total: 0 }))).toBe(true);
  });

  it("button disabled when the model greys every target pool: the modal would open with nothing to pick and the submit would be rejected", () => {
    const gb10 = node({ pool_label: "hami", gpu_model: "GB10" });
    expect(inSwitchablePool(gb10)).toBe(true);
    expect(canSwitchPool(gb10)).toBe(false);
  });
});
