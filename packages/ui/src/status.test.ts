/** 服务过渡态集合与 isServiceStatus 白名单回归。 */
import { describe, expect, it } from "vitest";

import {
  ALL_STATUS_MAPS,
  isNodeStatus,
  isServiceStatus,
  isTransientServiceStatus,
  serviceStatusMap,
  SEVERITY_ORDER,
  severityMap,
} from "./status";

describe("serviceStatusMap", () => {
  it("过渡态只有 deploying / stopping / releasing;unready 可能永远不就绪,不算过渡态", () => {
    const transient = Object.keys(serviceStatusMap).filter(isTransientServiceStatus);
    expect(transient).toEqual(["deploying", "stopping", "releasing"]);
    expect(isTransientServiceStatus("unready")).toBe(false);
  });

  it("isServiceStatus 只认表里的值(URL 上的 ?status= 靠它做白名单)", () => {
    expect(isServiceStatus("running")).toBe(true);
    expect(isServiceStatus("creating")).toBe(false);
    expect(isServiceStatus("toString")).toBe(false);
    expect(isServiceStatus(1)).toBe(false);
  });
});

describe("nodeStatusMap / severityMap", () => {
  it("isNodeStatus 只认表里的值(/nodes?status= 白名单靠它)", () => {
    expect(isNodeStatus("Cordoned")).toBe(true);
    expect(isNodeStatus("ready")).toBe(false);
    expect(isNodeStatus(null)).toBe(false);
  });

  it("SEVERITY_ORDER 恰好覆盖 severityMap 的键", () => {
    expect([...SEVERITY_ORDER].sort()).toEqual(Object.keys(severityMap).sort());
  });

  it("没有任何状态表条目还带 animated(徽标动效由 badge=processing 承担)", () => {
    for (const map of ALL_STATUS_MAPS) {
      for (const meta of Object.values<Record<string, unknown>>(map)) expect(meta).not.toHaveProperty("animated");
    }
  });
});
