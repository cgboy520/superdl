/** 节点路由深链与 q/pool/status 筛选白名单测试。 */
import { describe, expect, it } from "vitest";

import { Route } from "./nodes";

const validate = Route.options.validateSearch as (search: Record<string, unknown>) => Record<string, unknown>;

describe("nodes validateSearch", () => {
  it("node/q/pool 非空即收,status 走节点状态白名单", () => {
    expect(validate({ node: "gpu-a3-01", q: "gpu", pool: "hami", status: "Cordoned" })).toEqual({
      node: "gpu-a3-01",
      q: "gpu",
      pool: "hami",
      status: "Cordoned",
    });
  });

  it("空值与非法状态剥离,未知参数不落", () => {
    expect(validate({ node: "", q: "  ", pool: "", status: "Broken", foo: "bar" })).toEqual({});
  });
});
