import { describe, expect, it } from "vitest";

import { ALL_ROLES, canSeeMenu } from "./menu";

describe("canSeeMenu(菜单角色过滤,与后端 require_roles 对齐)", () => {
  it("finance 看不到节点页与平台配置", () => {
    expect(canSeeMenu("/nodes", "finance")).toBe(false);
    expect(canSeeMenu("/platform", "finance")).toBe(false);
    expect(canSeeMenu("/finance", "finance")).toBe(true);
  });
  it("ops 看不到财务页与平台配置", () => {
    expect(canSeeMenu("/finance", "ops")).toBe(false);
    expect(canSeeMenu("/platform", "ops")).toBe(false);
    expect(canSeeMenu("/nodes", "ops")).toBe(true);
  });
  it("readonly 除平台配置外全可见(只读)", () => {
    expect(canSeeMenu("/platform", "readonly")).toBe(false);
    expect(canSeeMenu("/finance", "readonly")).toBe(true);
    expect(canSeeMenu("/nodes", "readonly")).toBe(true);
  });
  it("admin 全部可见", () => {
    for (const key of ["/", "/nodes", "/skus", "/tenants", "/finance", "/audit", "/platform"]) {
      expect(canSeeMenu(key, "admin")).toBe(true);
    }
  });
  it("未知角色/未知菜单一律不可见", () => {
    expect(canSeeMenu("/finance", "hacker")).toBe(false);
    expect(canSeeMenu("/not-exist", "admin")).toBe(false);
  });
  it("角色全集覆盖总览页", () => {
    for (const role of ALL_ROLES) expect(canSeeMenu("/", role)).toBe(true);
  });
});
