/** 英文界面的语言切换冒烟。 */
import { expect, test } from "@playwright/test";

test.use({ locale: "en-US" });

test("语言切换器可将界面切回中文", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Log in to SuperDL" })).toBeVisible();
  await page.getByLabel("language").click();
  await page.locator(".ant-select-item-option").filter({ hasText: "中文" }).click();
  await expect(page.getByRole("heading", { name: "登录 SuperDL" })).toBeVisible();
});
