/** 切池目标候选与 MIG 机型判据测试。 */

import { describe, expect, it } from "vitest";

import { type NodeRow } from "../../api";
import { supportsMig } from "../../lib/pools";
import { currentPool, switchTargets } from "./-SwitchPoolModal";

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

describe("supportsMig", () => {
  it("带容量后缀的 canonical 按家族判", () => {
    expect(supportsMig("A100-80G")).toBe(true);
    expect(supportsMig("H200-141G")).toBe(true);
    expect(supportsMig("A30")).toBe(true);
  });

  it("不支持 MIG 的机型与未识别一律 false", () => {
    expect(supportsMig("GB10")).toBe(false);
    expect(supportsMig("RTX4090")).toBe(false);
    expect(supportsMig("L40S")).toBe(false);
    expect(supportsMig(undefined)).toBe(false);
  });
});

describe("switchTargets", () => {
  it("排除当前池,不支持 MIG 的机型把 mig 灰置而非隐藏", () => {
    expect(switchTargets(node())).toEqual([
      { pool: "kata", disabled: false },
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
