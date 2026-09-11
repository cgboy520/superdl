/** 服务过渡态集合与 isServiceStatus 白名单回归。 */
import { describe, expect, it } from "vitest";

import {
  isServiceStatus,
  isTransientServiceStatus,
  serviceStatusMap,
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
