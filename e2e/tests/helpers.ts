/** 浏览器冒烟的 UI 与 API 前置工具。 */

import { expect, type Locator, type Page } from "@playwright/test";

/** 点击对话框中最后一个「确定」按钮。 */
export async function confirmOk(page: Page): Promise<void> {
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /^确\s*定$/ })
    .last()
    .click();
}

/** 用时间戳与随机数生成测试手机号。 */
export function uniquePhone(): string {
  const ms = String(Date.now()).slice(-5);
  const rand = String(Math.floor(Math.random() * 1000)).padStart(3, "0");
  return `139${ms}${rand}`;
}

/** 生成随机字节的 SSH ed25519 公钥测试数据。 */
export function genEd25519Key(): string {
  const type = "ssh-ed25519";
  const typeBytes = new TextEncoder().encode(type);
  const keyBytes = crypto.getRandomValues(new Uint8Array(32));
  const blob = new Uint8Array(4 + typeBytes.length + 4 + 32);
  const dv = new DataView(blob.buffer);
  dv.setUint32(0, typeBytes.length);
  blob.set(typeBytes, 4);
  dv.setUint32(4 + typeBytes.length, 32);
  blob.set(keyBytes, 8 + typeBytes.length);
  let bin = "";
  for (const b of blob) bin += String.fromCharCode(b);
  const b64 = btoa(bin);
  return `${type} ${b64} e2e@smoke`;
}

/** 注册一个新号并落到已登录态。 */
export async function registerViaUi(page: Page, phone: string): Promise<void> {
  await page.goto("/login");
  await page.getByRole("button", { name: "免费注册" }).click();
  await page.getByPlaceholder("手机号").fill(phone);
  await page.getByRole("button", { name: "获取验证码" }).click();
  await page.getByPlaceholder("短信验证码").fill("123456");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page).not.toHaveURL(/login/, { timeout: 15_000 });
}

/** 经 API 建号并注入登录态:access token 由 addInitScript 写入 localStorage,refresh cookie 由浏览器托管;返回 access token。 */
export async function loginViaApi(page: Page, phone: string): Promise<string> {
  const code = await page.request.post("/api/v1/auth/sms-code", {
    data: { phone, purpose: "register" },
  });
  expect(code.status(), await code.text()).toBe(204);
  const resp = await page.request.post("/api/v1/auth/register", {
    data: { phone, sms_code: "123456", accept_terms: true },
  });
  expect(resp.status(), await resp.text()).toBe(201);
  const data = (await resp.json()) as { access_token: string };
  await page.addInitScript((token) => window.localStorage.setItem("superdl.web.accessToken", token), data.access_token);
  return data.access_token;
}

/** mock 渠道经 API 充值:建单 → mock 回调标记支付成功(需 dev 的 payment_mock 开启)。 */
export async function rechargeViaApi(page: Page, token: string, amount: string): Promise<void> {
  const order = await page.request.post("/api/v1/wallet/recharges", {
    headers: { Authorization: `Bearer ${token}` },
    data: { amount, channel: "mock" },
  });
  expect(order.status(), await order.text()).toBe(201);
  const { order_no } = (await order.json()) as { order_no: string };
  const paid = await page.request.post("/api/v1/webhooks/mock", {
    data: { order_no, amount, txn_id: `tx-${order_no}` },
  });
  expect(paid.status(), await paid.text()).toBe(200);
}

/** 经 API 添加一把 e2e 公钥。 */
export async function addSshKeyViaApi(page: Page, token: string): Promise<void> {
  const resp = await page.request.post("/api/v1/ssh-keys", {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: "e2e-key", public_key: genEd25519Key() },
  });
  expect(resp.status(), await resp.text()).toBe(201);
}

/** mock 渠道充值并关闭弹窗;未传 amount 时使用弹窗默认金额。 */
export async function rechargeViaUi(page: Page, amount?: string): Promise<void> {
  await page.goto("/billing");
  await page
    .getByRole("button", { name: /^充\s*值$/ })
    .first()
    .click();
  if (amount) await page.getByRole("dialog").getByRole("spinbutton").fill(amount);
  await page.getByRole("button", { name: "生成支付二维码" }).click();
  await page.getByRole("button", { name: /模拟支付成功/ }).click();
  await expect(page.getByText(/已到账/)).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press("Escape");
}

/** 经 UI 添加一把 SSH 公钥。 */
export async function addSshKeyViaUi(page: Page): Promise<void> {
  await page.goto("/settings");
  await page.getByLabel("名称").fill("e2e-key");
  await page.getByLabel("公钥内容").fill(genEd25519Key());
  await page.getByRole("button", { name: "添加公钥" }).click();
  await expect(page.getByText("公钥已添加")).toBeVisible({ timeout: 10_000 });
}

/** 选中市场页首个「共享·标准」SKU。 */
export async function pickSharedStandardSku(page: Page): Promise<void> {
  await page.goto("/market");
  const skuRow = page.locator("[data-row-key]", { hasText: "共享·标准" }).first();
  await expect(skuRow).toBeVisible({ timeout: 15_000 });
  await skuRow.getByRole("radio").check();
}

/** 填写自定义镜像并勾选 e2e 公钥,不提交表单。 */
export async function fillCustomImageForm(page: Page): Promise<void> {
  await page.getByText("自定义镜像").click();
  await page.getByPlaceholder("registry.example.com/your/image:tag").fill("registry.superdl.local/pytorch:2.9.0-cu128");
  await page.getByRole("checkbox", { name: /e2e-key/ }).check();
}

/** 实例列表等首行进入「运行中」,返回首行。 */
export async function waitFirstRowRunning(page: Page): Promise<Locator> {
  await expect(page).toHaveURL(/instances/, { timeout: 20_000 });
  const row = page.locator("[data-row-key]").first();
  await expect(row.getByText("运行中")).toBeVisible({ timeout: 90_000 });
  return row;
}

export async function setupUser(page: Page, amount: string): Promise<string> {
  const token = await loginViaApi(page, uniquePhone());
  await rechargeViaApi(page, token, amount);
  await addSshKeyViaApi(page, token);
  return token;
}
