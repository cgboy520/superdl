/** 跨 spec 文件共用的小工具与「同款前置」步骤。
 *
 * UI 前置(registerViaUi/rechargeViaUi/addSshKeyViaUi)只由 smoke 使用,守住 UI 链路本身;
 * 其余 spec 用 API 直达版(loginViaApi/rechargeViaApi/addSshKeyViaApi)跳过 UI 步骤,
 * 省下每条几十秒,也消掉与用例无关的失败点。 */

import { expect, type Locator, type Page } from "@playwright/test";

/**
 * 生成一个本轮唯一的测试手机号(139 + 8 位)。
 * 必须带随机位而不能只用 Date.now():playwright 按文件并行,同毫秒起跑的两个 spec 会拿到同一个号。
 * 毫秒后 5 位 + 3 位随机:保留时间前缀便于按时间找出测试用户。
 */
export function uniquePhone(): string {
  const ms = String(Date.now()).slice(-5);
  const rand = String(Math.floor(Math.random() * 1000)).padStart(3, "0");
  return `139${ms}${rand}`;
}

/** 构造合法且指纹唯一的 ed25519 公钥(与后端 blob 校验一致)。 */
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
  await page.getByText("注册", { exact: true }).click();
  await page.getByPlaceholder("手机号").fill(phone);
  await page.getByRole("button", { name: "获取验证码" }).click();
  await page.getByPlaceholder("短信验证码").fill("123456");
  await page.getByRole("checkbox").check(); // 同意用户协议/隐私政策
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page).not.toHaveURL(/login/, { timeout: 15_000 });
}

/** 经 API 建号并注入登录态:access token 由 addInitScript 在应用脚本前写入 localStorage,
 *  refresh cookie 经 context 共享的 cookie jar 由浏览器托管。返回 access token 供后续 API 前置用。 */
export async function loginViaApi(page: Page, phone: string): Promise<string> {
  const code = await page.request.post("/api/v1/auth/sms-code", {
    // dev 环境安全策略 captcha_enabled 默认关闭:发码不带人机校验 token
    data: { phone, purpose: "register" },
  });
  expect(code.status(), await code.text()).toBe(204);
  const resp = await page.request.post("/api/v1/auth/register", {
    data: { phone, sms_code: "123456", accept_terms: true },
  });
  expect(resp.status(), await resp.text()).toBe(201);
  const data = (await resp.json()) as { access_token: string };
  // 键名对齐 apps/web stores/auth.ts 的 TOKEN_KEY(函数体序列化进浏览器,无法引用本模块常量)
  await page.addInitScript(
    (token) => window.localStorage.setItem("superdl.web.accessToken", token),
    data.access_token,
  );
  return data.access_token;
}

/** mock 渠道经 API 充值:建单 → mock 回调标记支付成功(需 dev 的 payment_mock 开启,同 UI 链路)。 */
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

/** 经 API 添加一把 e2e 公钥(开发机形态创建时必须选一把)。 */
export async function addSshKeyViaApi(page: Page, token: string): Promise<void> {
  const resp = await page.request.post("/api/v1/ssh-keys", {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: "e2e-key", public_key: genEd25519Key() },
  });
  expect(resp.status(), await resp.text()).toBe(201);
}

/** mock 渠道充值并关掉弹窗。amount 省略 = 用弹窗默认额(¥100);
 *  包周期一次性预扣整段周期,默认额不够,须显式给大额。 */
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

/** 添加一把 SSH 公钥(开发机形态创建时必须选一把)。 */
export async function addSshKeyViaUi(page: Page): Promise<void> {
  await page.goto("/settings");
  await page.getByLabel("名称").fill("e2e-key");
  await page.getByLabel("公钥内容").fill(genEd25519Key());
  await page.getByRole("button", { name: "添加公钥" }).click();
  await expect(page.getByText("公钥已添加")).toBeVisible({ timeout: 10_000 });
}

/** 市场页选中「共享·标准」那条 SKU;计费方式与形态分叉由各 spec 自己接。 */
export async function pickSharedStandardSku(page: Page): Promise<void> {
  await page.goto("/market");
  const skuRow = page.locator(".ant-table-row", { hasText: "共享·标准" }).first();
  await expect(skuRow).toBeVisible({ timeout: 15_000 });
  await skuRow.getByRole("radio").check();
}

/** 创建页「自定义镜像」表单块:切自定义镜像 + 填 e2e 镜像 + 勾选 e2e 公钥。
 *  提交按钮文案随计费方式分叉(创建并开机/支付并创建),由各 spec 自己点。 */
export async function fillCustomImageForm(page: Page): Promise<void> {
  await page.getByText("自定义镜像").click();
  await page
    .getByPlaceholder("registry.example.com/your/image:tag")
    .fill("registry.superdl.local/pytorch:2.9.0-cu128");
  await page.getByRole("checkbox", { name: /e2e-key/ }).check();
}

/** 实例列表等首行进入「运行中」(worker+reconciler 推进),返回首行供后续行内断言。 */
export async function waitFirstRowRunning(page: Page): Promise<Locator> {
  await expect(page).toHaveURL(/instances/, { timeout: 20_000 });
  const row = page.locator(".ant-table-row").first();
  await expect(row.getByText("运行中")).toBeVisible({ timeout: 90_000 });
  return row;
}
