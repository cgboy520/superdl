/** 挂了说明:段状态派生回到「首屏全红」或漏掉提交后的错误标记。 */
import { describe, expect, it } from "vitest";

import { deriveSectionStatus } from "./SectionRail";

describe("deriveSectionStatus", () => {
  const sections = [
    { id: "a", title: "A", issue: null, touched: true },
    { id: "b", title: "B", issue: "缺镜像", touched: false },
    { id: "c", title: "C", issue: "缺端口", touched: false },
    { id: "d", title: "D", issue: null, touched: false },
  ];

  it("首屏:第一个有问题的段 process,未触碰的问题段 wait,不出 error", () => {
    expect(deriveSectionStatus(sections)).toEqual(["finish", "process", "wait", "wait"]);
  });

  it("触碰过的问题段才 error;点过提交后全部问题段 error", () => {
    const touched = sections.map((s) => (s.id === "c" ? { ...s, touched: true } : s));
    expect(deriveSectionStatus(touched)).toEqual(["finish", "process", "error", "wait"]);
    expect(deriveSectionStatus(sections, true)).toEqual(["finish", "process", "error", "wait"]);
  });

  it("全部无问题:触碰过 finish,未触碰 wait", () => {
    expect(deriveSectionStatus(sections.map((s) => ({ ...s, issue: null })))).toEqual([
      "finish",
      "wait",
      "wait",
      "wait",
    ]);
  });
});
