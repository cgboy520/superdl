/** tenants 路由 validateSearch:tab/q/dtab/istatus/inode 白名单与序列化往返。
 *  挂了 = Tab/检索词不再落 URL(刷新/分享丢状态),或非法值污染查询参数。 */
import { describe, expect, it } from "vitest";

import { Route } from "./tenants";

const validate = Route.options.validateSearch as (
  search: Record<string, unknown>,
) => Record<string, unknown>;

describe("tenants validateSearch", () => {
  it("tab/q 序列化往返:合法值原样保留", () => {
    const out = validate({ tab: "instances", q: "13800001111", dtab: "quota" });
    expect(out).toEqual({ tab: "instances", q: "13800001111", dtab: "quota" });
  });

  it("非法/空值剥离:白名单外的 tab、空 q、未知参数一律不落", () => {
    const out = validate({ tab: "hacked", q: "", dtab: "nope", foo: "bar" });
    expect(out).toEqual({});
  });

  it("实例筛选 istatus/inode:istatus 走实例状态白名单,inode 非空即收", () => {
    expect(validate({ istatus: "running", inode: "gpu-a3-01" })).toEqual({
      istatus: "running",
      inode: "gpu-a3-01",
    });
    expect(validate({ istatus: "bogus" })).toEqual({});
  });
});
