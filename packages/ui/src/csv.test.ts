import { describe, expect, it } from "vitest";

import { csvCell, csvOf } from "./csv";

describe("csvOf", () => {
  it("quotes separators and doubles quotes", () => {
    expect(csvOf([["a,b", 'say "hi"', "plain"]])).toBe('"a,b","say ""hi""",plain\r\n');
  });
  it("defuses spreadsheet formulas", () => {
    expect(csvCell("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(csvCell("-1+2")).toBe("'-1+2");
    expect(csvCell("ab-c")).toBe("ab-c");
  });
});
