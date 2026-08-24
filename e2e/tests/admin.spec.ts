/**
 * 管理端冒烟(P1-41a):登录 → 首登强制 MFA 绑定(TOTP 由 e2e 现算,不开服务端测试后门)
 * → 二要素登录 → 调账双人复核(发起:seed admin;复核:finance,经 API 建号)→ 冻结租户。
 *
 * 门控(无浏览器 CI 环境默认跳过;本地可跑):
 *   SUPERDL_ADMIN_E2E=1 pnpm --filter @superdl/e2e exec playwright test admin
 * 前置:API(8000,已迁移 + seed_dev)在跑;admin dev server(5174)由 playwright.config
 * 在门控开启时自动拉起。可用环境变量:SUPERDL_E2E_API / SUPERDL_ADMIN_BASE /
 * SUPERDL_ADMIN_USER / SUPERDL_ADMIN_PASSWORD(默认对齐 scripts/seed_dev.py)。
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { fillTotp } from "./totp";

test.skip(
  !process.env.SUPERDL_ADMIN_E2E,
  "管理端 e2e 门控:SUPERDL_ADMIN_E2E=1 开启(需 API 8000 已迁移 + seed)",
);

const API = process.env.SUPERDL_E2E_API ?? "http://localhost:8000";
const ADMIN = process.env.SUPERDL_ADMIN_BASE ?? "http://localhost:5174";
const SEED_ADMIN = {
  username: process.env.SUPERDL_ADMIN_USER ?? "admin",
  password: process.env.SUPERDL_ADMIN_PASSWORD ?? "admin123-dev",
};

/** 登录 → 首登强制绑定 TOTP:读取页面手动密钥,现算动态码完成绑定;返回密钥供后续登录。 */
async function loginAndBindMfa(page: Page, username: string, password: string): Promise<string> {
  await page.goto(`${ADMIN}/login`);
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
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
  return secret;
}

/** 已绑定账号的二要素登录(mfa_required 分支)。 */
async function loginWithMfa(page: Page, username: string, password: string, secret: string) {
  await page.evaluate(() => localStorage.clear());
  await page.goto(`${ADMIN}/login`);
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await fillTotp(page.getByPlaceholder("6 位动态码"), secret);
  await page.getByRole("button", { name: "验证并登录" }).click();
  await expect(page).not.toHaveURL(/login/, { timeout: 15_000 });
}

async function readToken(page: Page): Promise<string> {
  const token = await page.evaluate(() => localStorage.getItem("superdl.admin.accessToken"));
  expect(token).toBeTruthy();
  return token as string;
}

/** 经用户端 API 注册一个租户(mock 短信码固定 123456),返回 user_id。 */
async function registerTenant(request: APIRequestContext, phone: string): Promise<number> {
  const code = await request.post(`${API}/api/v1/auth/sms-code`, {
    data: { phone, purpose: "register" },
  });
  expect(code.status(), await code.text()).toBe(204);
  const resp = await request.post(`${API}/api/v1/auth/register`, {
    data: { phone, sms_code: "123456", accept_terms: true },
  });
  expect(resp.status(), await resp.text()).toBe(201);
  const data = (await resp.json()) as { user: { id: number } };
  return data.user.id;
}

test("管理端冒烟:MFA → 调账双人复核 → 冻结租户", async ({ page, request }) => {
  test.setTimeout(300_000);
  const phone = `137${String(Date.now()).slice(-8)}`;
  const maskedPhone = `${phone.slice(0, 3)}****${phone.slice(-4)}`;
  const tenantId = await registerTenant(request, phone);

  // ── 1. seed admin 首登:强制 MFA 绑定(TOTP 现算)────────────────────
  const adminSecret = await loginAndBindMfa(page, SEED_ADMIN.username, SEED_ADMIN.password);
  const adminToken = await readToken(page);

  // ── 2. 调账发起(admin 经 UI)────────────────────────────────────────
  await page.goto(`${ADMIN}/finance`);
  await page.getByRole("tab", { name: /调账/ }).click();
  await page.getByRole("button", { name: "发起调账" }).click();
  const createModal = page.locator(".ant-modal", { hasText: "发起调账" });
  await createModal.getByLabel("租户 ID").fill(String(tenantId));
  // 上下文回显(掩码手机号)出现后 OK 才可点
  await expect(createModal.getByText(maskedPhone)).toBeVisible({ timeout: 15_000 });
  await createModal.getByLabel(/金额/).fill("1.01");
  await createModal.getByLabel(/操作原因/).fill("e2e 冒烟调账");
  await createModal.getByRole("button", { name: /确\s*定/ }).click();
  await expect(page.getByText("调账单已发起,等待第二位管理员复核")).toBeVisible({
    timeout: 10_000,
  });
  const adjRow = page.locator(".ant-table-row", { hasText: "e2e 冒烟调账" });
  await expect(adjRow.getByText("待复核")).toBeVisible({ timeout: 10_000 });
  // 自复核禁手(双人制衡):发起人行的「通过」必须禁用
  await expect(adjRow.getByRole("button", { name: "通过" })).toBeDisabled();

  // ── 3. 复核人:finance 账号(经 API 建号,理由入审计)─────────────────
  const financeName = `fin-e2e-${String(Date.now()).slice(-6)}`;
  const created = await request.post(`${API}/api/admin/v1/admins`, {
    headers: { Authorization: `Bearer ${adminToken}` },
    data: {
      username: financeName,
      password: "finance-e2e-pass1",
      role: "finance",
      reason: "e2e 双人复核",
    },
  });
  expect(created.status(), await created.text()).toBe(201);

  // finance 首登同样强制绑定 TOTP;绑定后进调账页复核
  await loginAndBindMfa(page, financeName, "finance-e2e-pass1");
  await page.goto(`${ADMIN}/finance`);
  await page.getByRole("tab", { name: /调账/ }).click();
  const reviewRow = page.locator(".ant-table-row", { hasText: "e2e 冒烟调账" });
  await reviewRow.getByRole("button", { name: "通过" }).click();
  const reviewModal = page.locator(".ant-modal", { hasText: "通过调账(二次确认)" });
  await reviewModal.getByRole("button", { name: "通过" }).click();
  await expect(page.getByText("复核完成")).toBeVisible({ timeout: 10_000 });
  await expect(
    page.locator(".ant-table-row", { hasText: "e2e 冒烟调账" }).getByText("已生效"),
  ).toBeVisible({ timeout: 10_000 });

  // ── 4. 冻结租户(ops 写权限;seed admin 经已绑定 TOTP 重新登录)────────
  await loginWithMfa(page, SEED_ADMIN.username, SEED_ADMIN.password, adminSecret);
  await page.goto(`${ADMIN}/tenants`);
  const tenantRow = page.locator(".ant-table-row", { hasText: maskedPhone });
  await expect(tenantRow).toBeVisible({ timeout: 15_000 });
  await tenantRow.getByRole("button", { name: "冻结" }).click();
  const reasonModal = page.locator(".ant-modal", { hasText: "冻结租户" });
  await reasonModal.locator("textarea").fill("e2e 冒烟冻结");
  await reasonModal.getByRole("button", { name: "下一步" }).click();
  const confirmModal = page.locator(".ant-modal", { hasText: "二次确认" });
  await confirmModal.getByRole("button", { name: "确认执行" }).click();
  await expect(page.getByText(/已冻结,本次停止 \d+ 台运行中实例/)).toBeVisible({
    timeout: 15_000,
  });
  await expect(tenantRow.getByText("已冻结")).toBeVisible({ timeout: 15_000 });
});
