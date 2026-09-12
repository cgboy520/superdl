/** 可访问性静态扫描(axe-core):公开层全页 + 登录态控制台关键页。critical 违规即红,serious 只打印预警。前置同 smoke.spec。 */
import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { loginViaApi, uniquePhone } from "./helpers";

async function expectNoCritical(page: Page, name: string) {
  const results = await new AxeBuilder({ page }).analyze();
  const critical = results.violations.filter((v) => v.impact === "critical");
  const serious = results.violations.filter((v) => v.impact === "serious");
  if (serious.length > 0) {
    console.warn(`[a11y][${name}] serious 预警 ${serious.length} 条:`, serious.map((v) => v.id).join(", "));
  }
  expect(
    critical,
    `[a11y][${name}] critical 违规: ${critical.map((v) => `${v.id}(${v.nodes.length})`).join(", ")}`,
  ).toHaveLength(0);
}

test("公开层可访问性(落地页/登录/市场/帮助)", async ({ page }) => {
  for (const [path, name] of [
    ["/", "落地页"],
    ["/login", "登录"],
    ["/market", "算力市场"],
    ["/help", "帮助"],
  ] as const) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    await expectNoCritical(page, name);
  }
});

test("控制台关键页可访问性(实例/费用/设置)", async ({ page }) => {
  await loginViaApi(page, uniquePhone());
  for (const [path, name] of [
    ["/instances", "容器实例"],
    ["/billing", "费用中心"],
    ["/settings", "账户设置"],
  ] as const) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    await expectNoCritical(page, name);
  }
});
