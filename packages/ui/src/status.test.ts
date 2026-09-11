/** 服务过渡态集合与筛选项。挂了 = 列表对已删除/未就绪服务空转轮询,或筛选冒出已删除项。 */
import { describe, expect, it } from "vitest";

import {
  isServiceStatus,
  isTransientServiceStatus,
  SERVICE_FILTER_STATUSES,
  serviceStatusMap,
} from "./status";

describe("serviceStatusMap", () => {
  it("过渡态只有 deploying / stopping / releasing;unready 可能永远不就绪,不算过渡态", () => {
    const transient = Object.keys(serviceStatusMap).filter(isTransientServiceStatus);
    expect(transient).toEqual(["deploying", "stopping", "releasing"]);
    expect(isTransientServiceStatus("unready")).toBe(false);
  });

  it("筛选项覆盖除 released 外的全部状态,顺序与表一致", () => {
    expect(SERVICE_FILTER_STATUSES).toEqual(
      Object.keys(serviceStatusMap).filter((s) => s !== "released"),
    );
  });

  it("isServiceStatus 只认表里的值(URL 上的 ?status= 靠它做白名单)", () => {
    expect(isServiceStatus("running")).toBe(true);
    expect(isServiceStatus("creating")).toBe(false);
    expect(isServiceStatus("toString")).toBe(false);
    expect(isServiceStatus(1)).toBe(false);
  });

  it("unready 是 warning 而非 error,并带解释文案", () => {
    expect(serviceStatusMap.unready.badge).toBe("warning");
    expect(serviceStatusMap.unready.hintKey).toBeTruthy();
    expect(serviceStatusMap.failed.badge).toBe("error");
  });
});
