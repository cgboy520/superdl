/** finance 路由 validateSearch:结算缺口 g_kind/g_open 白名单与默认值剥离。挂了 = 缺口筛选不再落 URL,或非法值穿透到查询参数。 */
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
});
