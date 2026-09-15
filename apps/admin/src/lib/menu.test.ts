/** 菜单键集与角色可见性矩阵测试。 */
import { describe, expect, it } from "vitest";

import { MENU, MENU_ROLES, canSeeMenu } from "./menu";

describe("menu 单一事实源", () => {
  it("MENU 与 MENU_ROLES 键集一致(无漏登记/无残留)", () => {
    const menuKeys = MENU.map((m) => m.key).sort();
    const roleKeys = Object.keys(MENU_ROLES).sort();
    expect(menuKeys).toEqual(roleKeys);
  });

  it("canSeeMenu:finance 无节点/告警权限,告警页 admin/ops/readonly 可见", () => {
    expect(canSeeMenu("/nodes", "finance")).toBe(false);
    expect(canSeeMenu("/alerts", "finance")).toBe(false);
    expect(canSeeMenu("/alerts", "ops")).toBe(true);
    expect(canSeeMenu("/alerts", "admin")).toBe(true);
    expect(canSeeMenu("/alerts", "readonly")).toBe(true);
    expect(canSeeMenu("/platform", "ops")).toBe(false);
  });
});
