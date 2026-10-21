/** URL allow-list of the gateway page: tenant id and organization uuid. */
import { describe, expect, it } from "vitest";

import { netgwValidateSearch } from "./netgw";

describe("netgw validateSearch", () => {
  it("keeps a positive tenant id and a 32-hex organization uuid; drops anything else", () => {
    const org = "0123456789abcdef0123456789abcdef";
    expect(netgwValidateSearch({ user_id: "7", org })).toEqual({ user_id: 7, org });
    expect(netgwValidateSearch({ user_id: "-1", org: "acme" })).toEqual({});
  });
});
