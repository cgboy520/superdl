/** Private network page: gateway and scope allow-list. */
import { describe, expect, it } from "vitest";

import { networkValidateSearch } from "./_console.network";

describe("networkValidateSearch", () => {
  it("keeps a 32-hex gateway and the organization scope; drops anything else", () => {
    const gw = "0123456789abcdef0123456789abcdef";
    expect(networkValidateSearch({ gw, scope: "org" })).toEqual({ gw, scope: "org" });
    expect(networkValidateSearch({ gw })).toEqual({ gw });
    expect(networkValidateSearch({ scope: "personal" })).toEqual({});
    expect(networkValidateSearch({ gw: "zz" })).toEqual({});
  });
});
