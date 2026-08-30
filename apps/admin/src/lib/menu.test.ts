/** 菜单单一事实源守护:MENU(侧栏/命令面板共用)与 MENU_ROLES(权限表)键集必须一致——
 *  类型层有 satisfies 编译期约束,本测试防类型被 as/绕过后的运行时漂移;
 *  canSeeMenu 行为按角色矩阵断言(finance 无节点/告警入口,告警页 ops 可见)。 */
import { describe, expect, it } from "vitest";

import { MENU, MENU_ROLES, canSeeMenu } from "./menu";

describe("menu 单一事实源", () => {
  it("MENU 与 MENU_ROLES 键集一致(无漏登记/无残留)", () => {
    const menuKeys = MENU.map((m) => m.key).sort();
    const roleKeys = Object.keys(MENU_ROLES).sort();
    expect(menuKeys).toEqual(roleKeys);
  });

  it("MENU 每项都有非空 labelKey(i18n 键,命令面板与侧栏共用)", () => {
    for (const m of MENU) {
      expect(m.labelKey).toMatch(/^menu\.\w+$/);
    }
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
