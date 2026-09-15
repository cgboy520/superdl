/** 财务路由对账日、Tab 与结算缺口筛选的 URL 解析测试。 */
import { describe, expect, it } from "vitest";

import { Route } from "./finance";

const validate = Route.options.validateSearch as (search: Record<string, unknown>) => Record<string, unknown>;

describe("finance validateSearch(结算缺口)", () => {
  it("g_kind 走类型白名单,g_open 只认 0(默认只看未核销不入 URL)", () => {
    expect(validate({ tab: "gaps", g_kind: "daily_disk", g_open: "0" })).toEqual({
      tab: "gaps",
      g_kind: "daily_disk",
      g_open: "0",
    });
    expect(validate({ g_kind: "weekly", g_open: "1" })).toEqual({});
  });

  it("对账日 day 只收 YYYY-MM-DD", () => {
    expect(validate({ day: "2026-09-13" })).toEqual({ day: "2026-09-13" });
    expect(validate({ day: "2026-9-13" })).toEqual({});
  });

  it("审计已独立成页:?tab=audit 不再是合法 Tab(回落订单)", () => {
    expect(validate({ tab: "audit" })).toEqual({});
    expect(validate({ tab: "anomalies" })).toEqual({ tab: "anomalies" });
  });
});
