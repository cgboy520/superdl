/** 管理端最小冒烟:登录页能打开、seed admin 首登强制 MFA 绑定(TOTP 由 e2e 现算)后进控制台。门控 `SUPERDL_ADMIN_E2E=1 pnpm --filter @superdl/e2e exec playwright test admin`;前置 API(8000,已迁移 + seed_dev)在跑,admin dev server(5174)由 playwright.config 拉起;环境变量 SUPERDL_ADMIN_BASE / SUPERDL_ADMIN_USER / SUPERDL_ADMIN_PASSWORD(默认对齐 scripts/seed_dev.py)。 */
import { expect, test, type Page } from "@playwright/test";

import { fillTotp } from "./totp";

test.skip(
  !process.env.SUPERDL_ADMIN_E2E,
  "管理端 e2e 门控:SUPERDL_ADMIN_E2E=1 开启(需 API 8000 已迁移 + seed)",
);

const ADMIN = process.env.SUPERDL_ADMIN_BASE ?? "http://localhost:5174";
const SEED_ADMIN = {
  username: process.env.SUPERDL_ADMIN_USER ?? "admin",
  password: process.env.SUPERDL_ADMIN_PASSWORD ?? "admin123-dev",
};

// antd 两字按钮会插空格,用 /^X\s*Y$/ 匹配
/** 登录 → 首登强制绑定 TOTP:读页面手动密钥,现算动态码完成绑定,进入控制台。 */
async function loginAndBindMfa(page: Page, username: string, password: string): Promise<void> {
  await page.goto(`${ADMIN}/login`);
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: /^登\s*录$/ }).click();
  // 绑定页:二维码 + 手动录入密钥(code 元素全页唯一)
  const secretEl = page.locator("code").first();
  await expect(secretEl).toBeVisible({ timeout: 15_000 });
  const secret = (await secretEl.innerText()).trim();
  expect(secret.length).toBeGreaterThanOrEqual(16);
  await fillTotp(page.getByPlaceholder("6 位动态码"), secret);
  await page.getByRole("button", { name: "完成绑定并登录" }).click();
  // 恢复码页(仅此一次):勾选已离线保存 → 进入控制台
  await page.getByRole("checkbox", { name: "我已将恢复码离线妥善保存" }).check();
  await page.getByRole("button", { name: "进入控制台" }).click();
  await expect(page).not.toHaveURL(/login/, { timeout: 15_000 });
}

test("管理端冒烟:登录页能打开、seed admin 能进控制台", async ({ page }) => {
  // 登录页能打开
  await page.goto(`${ADMIN}/login`);
  await expect(page.getByRole("button", { name: /^登\s*录$/ })).toBeVisible({ timeout: 15_000 });

  // seed admin 首登:强制 MFA 绑定 → 进入控制台
  await loginAndBindMfa(page, SEED_ADMIN.username, SEED_ADMIN.password);
});
