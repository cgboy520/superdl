/** 管理端冒烟:首次 MFA 绑定、登录与控制台导航;由 SUPERDL_ADMIN_E2E 门控。 */
import { expect, test, type Page } from "@playwright/test";

import { fillTotp } from "./totp";

test.skip(!process.env.SUPERDL_ADMIN_E2E, "管理端 e2e 门控:SUPERDL_ADMIN_E2E=1 开启(需 API 8000 已迁移 + seed)");

const ADMIN = process.env.SUPERDL_ADMIN_BASE ?? "http://localhost:5174";
const SEED_ADMIN = {
  username: process.env.SUPERDL_ADMIN_USER ?? "admin",
  password: process.env.SUPERDL_ADMIN_PASSWORD ?? "admin123-dev",
};

/** 登录 → 首登强制绑定 TOTP:读页面手动密钥,现算动态码完成绑定,进入控制台。 */
async function loginAndBindMfa(page: Page, username: string, password: string): Promise<void> {
  await page.goto(`${ADMIN}/login`);
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: /^登\s*录$/ }).click();
  const secretEl = page.getByTestId("mfa-secret");
  await expect(secretEl).toBeVisible({ timeout: 15_000 });
  const secret = (await secretEl.innerText()).trim();
  expect(secret.length).toBeGreaterThanOrEqual(16);
  await fillTotp(page.getByPlaceholder("6 位动态码"), secret);
  await page.getByRole("button", { name: "完成绑定并登录" }).click();
  await page.getByRole("checkbox", { name: "我已将恢复码离线妥善保存" }).check();
  await page.getByRole("button", { name: "进入控制台" }).click();
  await expect(page).not.toHaveURL(/login/, { timeout: 15_000 });
}

test("管理端冒烟:登录页能打开、seed admin 能进控制台", async ({ page }) => {
  await page.goto(`${ADMIN}/login`);
  await expect(page.getByRole("button", { name: /^登\s*录$/ })).toBeVisible({ timeout: 15_000 });

  await loginAndBindMfa(page, SEED_ADMIN.username, SEED_ADMIN.password);
});
