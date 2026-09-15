/** 告警路由 severity/acked/type 白名单与 URL 往返测试。 */
import { describe, expect, it } from "vitest";

import { Route } from "./alerts";

const validate = Route.options.validateSearch as (search: Record<string, unknown>) => Record<string, unknown>;

describe("alerts validateSearch", () => {
  it("severity/acked/type 合法值原样保留", () => {
    expect(validate({ severity: "critical", acked: "unacked", type: "gpu_fault" })).toEqual({
      severity: "critical",
      acked: "unacked",
      type: "gpu_fault",
    });
    expect(validate({ severity: "info", acked: "acked", type: "admin_alert" })).toEqual({
      severity: "info",
      acked: "acked",
      type: "admin_alert",
    });
  });

  it("非法/空值剥离:白名单外的级别、状态与类型、未知参数一律不落", () => {
    expect(validate({ severity: "fatal", acked: "maybe", type: "whatever", foo: "bar" })).toEqual({});
    expect(validate({ severity: "", acked: "", type: "" })).toEqual({});
  });
});
