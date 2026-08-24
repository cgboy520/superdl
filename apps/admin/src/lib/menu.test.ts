import { describe, expect, it } from "vitest";

import { canSeeMenu } from "./menu";

describe("canSeeMenu(菜单角色过滤,与后端 require_roles 对齐)", () => {
  it("finance 看不到节点页/集群页/镜像页与平台配置", () => {
    expect(canSeeMenu("/nodes", "finance")).toBe(false);
    expect(canSeeMenu("/cluster", "finance")).toBe(false);
    expect(canSeeMenu("/images", "finance")).toBe(false);
    expect(canSeeMenu("/platform", "finance")).toBe(false);
    expect(canSeeMenu("/finance", "finance")).toBe(true);
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
});
