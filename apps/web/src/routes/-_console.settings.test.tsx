/** Account settings: tab allow-list and hash deep-link mapping. */
import { describe, expect, it } from "vitest";

import { settingsValidateSearch, tabOfHash } from "./_console.settings";

describe("settingsValidateSearch", () => {
  it("the four valid tabs are kept; unknown values and non-strings are stripped (the page defaults to ssh)", () => {
    for (const tab of ["ssh", "notify", "realname", "account"]) {
      expect(settingsValidateSearch({ tab })).toEqual({ tab });
    }
    expect(settingsValidateSearch({ tab: "billing" })).toEqual({});
    expect(settingsValidateSearch({ tab: 3 })).toEqual({});
    expect(settingsValidateSearch({})).toEqual({});
  });
});

describe("tabOfHash", () => {
  it("#ssh / #notify map to their tabs (the billing balance card change deep link); other hashes do not take the tab", () => {
    expect(tabOfHash("ssh")).toBe("ssh");
    expect(tabOfHash("#notify")).toBe("notify");
    expect(tabOfHash("")).toBeUndefined();
    expect(tabOfHash("#pricing")).toBeUndefined();
  });
});
