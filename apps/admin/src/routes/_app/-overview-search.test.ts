/** 总览路由告警 severity 白名单测试。 */
import { describe, expect, it } from "vitest";

import { Route } from "./index";

const validate = Route.options.validateSearch as (search: Record<string, unknown>) => Record<string, unknown>;

describe("overview validateSearch", () => {
  it("severity 三级合法值原样保留", () => {
    expect(validate({ severity: "critical" })).toEqual({ severity: "critical" });
    expect(validate({ severity: "warning" })).toEqual({ severity: "warning" });
    expect(validate({ severity: "info" })).toEqual({ severity: "info" });
  });

  it("白名单外的级别、空值与未知参数一律不落", () => {
    expect(validate({ severity: "fatal", foo: "bar" })).toEqual({});
    expect(validate({ severity: "" })).toEqual({});
    expect(validate({})).toEqual({});
  });
});
