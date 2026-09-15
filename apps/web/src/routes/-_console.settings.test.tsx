/** 账户设置的 Tab 白名单与 hash 深链映射测试。 */
import { describe, expect, it } from "vitest";

import { settingsValidateSearch, tabOfHash } from "./_console.settings";

describe("settingsValidateSearch", () => {
  it("四个合法 tab 保留;未知值与非字符串一律剥离(默认 ssh 由页面兜底)", () => {
    for (const tab of ["ssh", "notify", "realname", "account"]) {
      expect(settingsValidateSearch({ tab })).toEqual({ tab });
    }
    expect(settingsValidateSearch({ tab: "billing" })).toEqual({});
    expect(settingsValidateSearch({ tab: 3 })).toEqual({});
    expect(settingsValidateSearch({})).toEqual({});
  });
});

describe("tabOfHash", () => {
  it("#ssh / #notify 映射到对应 Tab(费用中心余额卡「修改」深链);其余不夺 tab", () => {
    expect(tabOfHash("ssh")).toBe("ssh");
    expect(tabOfHash("#notify")).toBe("notify");
    expect(tabOfHash("")).toBeUndefined();
    expect(tabOfHash("#pricing")).toBeUndefined();
  });
});
