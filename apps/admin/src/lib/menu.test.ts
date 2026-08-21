import { describe, expect, it } from "vitest";

import { ALL_ROLES, MENU_ROLES, canSeeMenu } from "./menu";

describe("canSeeMenu(菜单角色过滤,与后端 require_roles 对齐)", () => {
  it("finance 看不到节点页/集群页/镜像页与平台配置", () => {
    expect(canSeeMenu("/nodes", "finance")).toBe(false);
    expect(canSeeMenu("/cluster", "finance")).toBe(false);
    expect(canSeeMenu("/images", "finance")).toBe(false);
    expect(canSeeMenu("/platform", "finance")).toBe(false);
    expect(canSeeMenu("/finance", "finance")).toBe(true);
  });
  it("镜像与预热页角色对齐后端 /images require_roles", () => {
    expect(MENU_ROLES["/images"]).toEqual(["admin", "ops", "readonly"]);
    expect(canSeeMenu("/images", "ops")).toBe(true);
    expect(canSeeMenu("/images", "readonly")).toBe(true);
  });
  it("ops 看不到财务页与平台配置", () => {
    expect(canSeeMenu("/finance", "ops")).toBe(false);
    expect(canSeeMenu("/platform", "ops")).toBe(false);
    expect(canSeeMenu("/nodes", "ops")).toBe(true);
    expect(canSeeMenu("/cluster", "ops")).toBe(true);
  });
  it("readonly 除平台配置外全可见(只读)", () => {
    expect(canSeeMenu("/platform", "readonly")).toBe(false);
    expect(canSeeMenu("/finance", "readonly")).toBe(true);
    expect(canSeeMenu("/nodes", "readonly")).toBe(true);
  });
  it("admin 全部可见", () => {
    for (const key of [
      "/",
      "/nodes",
      "/cluster",
      "/skus",
      "/tenants",
      "/finance",
      "/audit",
      "/platform",
    ] as const) {
      expect(canSeeMenu(key, "admin")).toBe(true);
    }
  });
  it("未知角色不可见(未知菜单 key 已由 MenuKey 类型在编译期拦截)", () => {
    expect(canSeeMenu("/finance", "hacker")).toBe(false);
  });
  it("角色全集覆盖总览页", () => {
    for (const role of ALL_ROLES) expect(canSeeMenu("/", role)).toBe(true);
  });
});
