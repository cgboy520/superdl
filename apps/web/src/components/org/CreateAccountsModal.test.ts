/** Batch account names and the one-time credentials CSV. */
import { describe, expect, it } from "vitest";

import { credentialsCsv, listNames, sequenceNames } from "./CreateAccountsModal";

describe("account names", () => {
  it("numbers a sequence with zero padding", () => {
    expect(sequenceNames("student", 9, 3, 2)).toEqual(["student09", "student10", "student11"]);
  });
  it("splits a pasted list on lines, commas and spaces, lowercased", () => {
    expect(listNames("Alice, bob\n\ncarol  dave;eve")).toEqual(["alice", "bob", "carol", "dave", "eve"]);
  });
});

describe("credentialsCsv", () => {
  it("writes a header and one row per account, defusing formula-like passwords", () => {
    expect(credentialsCsv([{ username: "lab.a", password: "=1+1-shared-pass" }])).toBe(
      "username,password\r\nlab.a,'=1+1-shared-pass\r\n",
    );
  });
});
