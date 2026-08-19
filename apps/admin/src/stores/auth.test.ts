// @vitest-environment jsdom
// (auth store 模块初始化即读 localStorage,node 环境无此全局)
import { describe, expect, it } from "vitest";

import { canWriteFinance, canWriteOps } from "./auth";

describe("角色写权限判定", () => {
  it("canWriteOps:仅 admin/ops", () => {
    expect(canWriteOps("admin")).toBe(true);
    expect(canWriteOps("ops")).toBe(true);
    expect(canWriteOps("finance")).toBe(false);
    expect(canWriteOps("readonly")).toBe(false);
  });
  it("canWriteFinance:仅 admin/finance", () => {
    expect(canWriteFinance("admin")).toBe(true);
    expect(canWriteFinance("finance")).toBe(true);
    expect(canWriteFinance("ops")).toBe(false);
    expect(canWriteFinance("readonly")).toBe(false);
  });
});
