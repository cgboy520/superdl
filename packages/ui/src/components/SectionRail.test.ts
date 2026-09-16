/** First paint, touch and submit states of form sections. */
import { describe, expect, it } from "vitest";

import { deriveSectionStatus } from "./SectionRail";

describe("deriveSectionStatus", () => {
  const sections = [
    { id: "a", title: "A", issue: null, touched: true },
    { id: "b", title: "B", issue: "image missing", touched: false },
    { id: "c", title: "C", issue: "port missing", touched: false },
    { id: "d", title: "D", issue: null, touched: false },
  ];

  it("first paint: the first section with an issue is process, untouched issue sections wait, no error", () => {
    expect(deriveSectionStatus(sections)).toEqual(["finish", "process", "wait", "wait"]);
  });

  it("only touched issue sections are error; after a submit attempt every issue section is error", () => {
    const touched = sections.map((s) => (s.id === "c" ? { ...s, touched: true } : s));
    expect(deriveSectionStatus(touched)).toEqual(["finish", "process", "error", "wait"]);
    expect(deriveSectionStatus(sections, true)).toEqual(["finish", "process", "error", "wait"]);
  });

  it("no issues: touched finish, untouched wait", () => {
    expect(deriveSectionStatus(sections.map((s) => ({ ...s, issue: null })))).toEqual([
      "finish",
      "wait",
      "wait",
      "wait",
    ]);
  });
});
