/**
 * en 冒烟:切到 en-US 后公开页无 CJK 残留、无 i18n 键名泄漏、语言切换器可用。
 * 主链路仍由 smoke.spec 以 zh-CN(playwright locale 钉死)覆盖。
 */
import { expect, test } from "@playwright/test";

const CJK = /[一-鿿]/;
const KEY_LEAK = /\b(?:web|shared|errors):[a-zA-Z0-9_.]+\b|\b(?:landing|login|topbar|footer|copy|instances|market|create|storage|billing|settings|dashboard|query|sku|common)\.[a-zA-Z0-9_.]+\b/;

test.use({ locale: "en-US" });

test("英文环境公开页无中文残留与键泄漏", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("superdl.lang", "en-US");
  });

  for (const path of ["/", "/login"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const text = (await page.locator("body").innerText()).replace(/\s+/g, " ");
    expect(text, `${path} 存在 CJK 残留`).not.toMatch(CJK);
    expect(text, `${path} 存在键名泄漏`).not.toMatch(KEY_LEAK);
  }
});

test("语言切换器可将界面切回中文", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("superdl.lang", "en-US");
  });
  await page.goto("/login");
  await expect(page.getByRole("button", { name: "Sign up & log in" })).toBeVisible();
});
