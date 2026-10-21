/** Organization page: tab and invitation allow-list. */
import { describe, expect, it } from "vitest";

import { orgValidateSearch } from "./_console.org";

describe("orgValidateSearch", () => {
  it("keeps the four tabs and a 32-hex invitation id; drops everything else", () => {
    for (const tab of ["overview", "members", "invitations", "finance"]) {
      expect(orgValidateSearch({ tab })).toEqual({ tab });
    }
    const invite = "0123456789abcdef0123456789abcdef";
    expect(orgValidateSearch({ invite, tab: "finance" })).toEqual({ invite, tab: "finance" });
    expect(orgValidateSearch({ tab: "ledger" })).toEqual({});
    expect(orgValidateSearch({ invite: "../x" })).toEqual({});
    expect(orgValidateSearch({ invite: invite.toUpperCase() })).toEqual({});
  });
});
