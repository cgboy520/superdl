/** Menu key set and role visibility matrix. */
import { describe, expect, it } from "vitest";

import { MENU, MENU_ROLES, canSeeMenu } from "./menu";

describe("menu single source of truth", () => {
  it("MENU and MENU_ROLES have the same key set (nothing unregistered / left over)", () => {
    const menuKeys = MENU.map((m) => m.key).sort();
    const roleKeys = Object.keys(MENU_ROLES).sort();
    expect(menuKeys).toEqual(roleKeys);
  });

  it("canSeeMenu: finance has no nodes/alerts access, the alerts page is visible to admin/ops/readonly", () => {
    expect(canSeeMenu("/nodes", "finance")).toBe(false);
    expect(canSeeMenu("/alerts", "finance")).toBe(false);
    expect(canSeeMenu("/alerts", "ops")).toBe(true);
    expect(canSeeMenu("/alerts", "admin")).toBe(true);
    expect(canSeeMenu("/alerts", "readonly")).toBe(true);
    expect(canSeeMenu("/platform", "ops")).toBe(false);
  });
});
