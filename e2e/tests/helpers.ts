/** 跨 spec 文件共用的小工具与「同款前置」步骤。

前置(注册 → 充值 → 公钥 → 市场选规格)四条 spec 逐字相同,抄在各自文件里的代价是:
改一处文案要同步改四遍,漏一遍就是一条随机红的用例。 */

import { expect, type Page } from "@playwright/test";

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
