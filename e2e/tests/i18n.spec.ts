/**
 * en 冒烟:语言切换器可用(浏览器专属行为)。
 * 「公开页无 CJK 残留」与「无键泄漏」已由 scripts/check-cjk.sh 与 localeParity 覆盖,不在此重测。
 * 主链路仍由 smoke.spec 以 zh-CN(playwright locale 钉死)覆盖。
 */
import { expect, test } from "@playwright/test";

test.use({ locale: "en-US" });

// 语言钉在 test.use 的 locale:detection 顺序 localStorage→navigator,无 localStorage 时必落 navigator
test("语言切换器可将界面切回中文", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Log in to SuperDL" })).toBeVisible();
  await page.getByLabel("language").click();
  // antd Select 的 role=option 是隐藏的 a11y 节点,点可见下拉项
  await page.locator(".ant-select-item-option").filter({ hasText: "中文" }).click();
  await expect(page.getByRole("heading", { name: "登录 SuperDL" })).toBeVisible();
});
