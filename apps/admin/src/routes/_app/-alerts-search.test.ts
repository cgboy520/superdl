/** alerts 路由 validateSearch:severity/acked 白名单与序列化往返。
 *  挂了 = 筛选不再落 URL(刷新/分享丢状态),或非法值穿透到服务端查询参数。
 *  文件名带 `-` 前缀:routes 目录下的非路由文件须以 `-` 开头,否则 router 插件警告。 */
import { describe, expect, it } from "vitest";

import { Route } from "./alerts";

const validate = Route.options.validateSearch as (
  search: Record<string, unknown>,
) => Record<string, unknown>;

describe("alerts validateSearch", () => {
  it("severity/acked 合法值原样保留", () => {
    expect(validate({ severity: "critical", acked: "unacked" })).toEqual({
      severity: "critical",
      acked: "unacked",
    });
    expect(validate({ severity: "info", acked: "acked" })).toEqual({
      severity: "info",
      acked: "acked",
    });
  });

  it("非法/空值剥离:白名单外的级别与状态、未知参数一律不落", () => {
    expect(validate({ severity: "fatal", acked: "maybe", foo: "bar" })).toEqual({});
    expect(validate({ severity: "", acked: "" })).toEqual({});
  });

  it("缺省为空对象:不带参数进入不产出任何查询串", () => {
    expect(validate({})).toEqual({});
  });
});
