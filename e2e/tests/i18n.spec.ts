/**
 * en 冒烟:切到 en-US 后公开页无 CJK 残留、无 i18n 键名泄漏、语言切换器可用。
 * 主链路仍由 smoke.spec 以 zh-CN(playwright locale 钉死)覆盖。
 */
import { expect, test } from "@playwright/test";

const CJK = /[一-鿿]/;
const KEY_LEAK = /\b(?:web|shared|errors):[a-zA-Z0-9_.]+\b|\b(?:landing|login|topbar|footer|copy|instances|market|create|storage|billing|settings|dashboard|query|sku|common)\.[a-zA-Z0-9_.]+\b/;

test.use({ locale: "en-US" });

// 语言钉在 test.use 的 locale:detection 顺序 localStorage→navigator,无 localStorage 时必落 navigator
test("英文环境公开页无中文残留与键泄漏", async ({ page }) => {
  for (const path of ["/", "/login"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const text = (await page.locator("body").innerText()).replace(/\s+/g, " ");
    expect(text, `${path} 存在 CJK 残留`).not.toMatch(CJK);
    expect(text, `${path} 存在键名泄漏`).not.toMatch(KEY_LEAK);
  }
});

test("语言切换器可将界面切回中文", async ({ page }) => {
  await page.goto("/login");
  await expect(
    page.getByRole("heading", { name: "Log in to SuperDL" }),
  ).toBeVisible();
  await page.getByLabel("language").click();
  // antd Select 的 role=option 是隐藏的 a11y 节点,点可见下拉项
  await page.locator(".ant-select-item-option").filter({ hasText: "中文" }).click();
  await expect(page.getByRole("heading", { name: "登录 SuperDL" })).toBeVisible();
});
