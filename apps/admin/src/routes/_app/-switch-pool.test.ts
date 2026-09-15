/** 切池目标候选与可切判据(机型能力 → 目标池灰置;观测卡数掉 0 仍可切回)。 */

import { describe, expect, it } from "vitest";

import { type NodeRow } from "../../api";
import { canSwitchPool, currentPool, switchTargets } from "./-SwitchPoolModal";

function node(over: Partial<NodeRow> = {}): NodeRow {
  return {
    name: "n1",
    pool_label: "hami",
    gpu_model: "RTX4090",
    gpu_total: 8,
    gpu_used: 0,
    status: "Ready",
    vcpu: 64,
    mem_gb: 256,
    disk_gb: 2048,
    ...over,
  };
}

describe("switchTargets", () => {
  it("排除当前池,不支持 MIG 的机型把 mig 灰置而非隐藏", () => {
    expect(switchTargets(node())).toEqual([
      { pool: "kata", disabled: false },
      { pool: "mig", disabled: true },
    ]);
  });

  it("集成 GPU 两个池都灰置,只剩 hami 可切", () => {
    expect(switchTargets(node({ gpu_model: "GB10" }))).toEqual([
      { pool: "kata", disabled: true },
      { pool: "mig", disabled: true },
    ]);
  });

  it("支持 MIG 的机型三选二都可用", () => {
    expect(switchTargets(node({ gpu_model: "H100-80G", pool_label: "kata" }))).toEqual([
      { pool: "hami", disabled: false },
      { pool: "mig", disabled: false },
    ]);
  });

  it("切池在途时当前池取期望池:候选里不再出现目标池自己", () => {
    const n = node({ pool_label: "hami", desired_pool: "kata", gpu_model: "H100-80G" });
    expect(currentPool(n)).toBe("kata");
    expect(switchTargets(n).map((o) => o.pool)).toEqual(["hami", "mig"]);
  });
});

describe("canSwitchPool", () => {
  it("cpu 池与未打标节点不可切", () => {
    expect(canSwitchPool(node({ pool_label: "cpu", gpu_total: 0 }))).toBe(false);
    expect(canSwitchPool(node({ pool_label: "", gpu_total: 0 }))).toBe(false);
  });

  it("观测卡数掉到 0 但还在 GPU 池:仍可切回,否则节点锁死在坏池里", () => {
    expect(canSwitchPool(node({ pool_label: "kata", gpu_model: "GB10", gpu_total: 0 }))).toBe(true);
  });
});
