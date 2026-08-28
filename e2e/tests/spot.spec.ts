/**
 * 竞价冒烟:市场页切竞价 → 折后价 → 创建页知情同意(不勾选关不掉)→ 建出竞价实例 →
 * 列表带「可回收」标记 →「更多」→ 转按量 → 标记消失。前置与 smoke 同款。
 *
 * 这条挂了通常说明:折扣没带进创建流、知情同意的闸松了,或转按量断了。
 */
import { expect, test } from "@playwright/test";

import { genEd25519Key, uniquePhone } from "./helpers";

test("买竞价并转按量", async ({ page }) => {
  test.setTimeout(300_000);
  const phone = uniquePhone();

  // ── 注册 + 充值
  await page.goto("/login");
  await page.getByText("注册", { exact: true }).click();
  await page.getByPlaceholder("手机号").fill(phone);
  await page.getByRole("button", { name: "获取验证码" }).click();
  await page.getByPlaceholder("短信验证码").fill("123456");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page).not.toHaveURL(/login/, { timeout: 15_000 });

  await page.goto("/billing");
  await page
    .getByRole("button", { name: /^充\s*值$/ })
    .first()
    .click();
  await page.getByRole("button", { name: "生成支付二维码" }).click();
  await page.getByRole("button", { name: /模拟支付成功/ }).click();
  await expect(page.getByText(/已到账/)).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press("Escape");

  await page.goto("/settings");
  await page.getByLabel("名称").fill("e2e-key");
  await page.getByLabel("公钥内容").fill(genEd25519Key());
  await page.getByRole("button", { name: "添加公钥" }).click();
  await expect(page.getByText("公钥已添加")).toBeVisible({ timeout: 10_000 });

  // ── 市场:切竞价 → 常驻警示 + 折后价
  await page.goto("/market");
  const skuRow = page.locator(".ant-table-row", { hasText: "共享·标准" }).first();
  await expect(skuRow).toBeVisible({ timeout: 15_000 });
  await skuRow.getByRole("radio").check();
  await page
    .getByRole("group", { name: "计费方式" })
    .getByRole("button", { name: /^竞\s*价/ })
    .click();
  // 可被回收这句必须常驻,不能只藏在知情同意里
  await expect(page.getByText(/可被平台回收|可被回收/).first()).toBeVisible();
  await page.getByRole("button", { name: "下一步:配置实例" }).click();
  await expect(page).toHaveURL(/market=spot/);

  // ── 创建页:提交前弹知情同意,不勾选过不去
  await page.getByText("自定义镜像").click();
  await page
    .getByPlaceholder("registry.example.com/your/image:tag")
    .fill("registry.superdl.local/pytorch:2.9.0-cu128");
  await page.getByRole("checkbox", { name: /e2e-key/ }).check();
  await page
    .getByRole("button", { name: /创建并开机|支付并创建/ })
    .first()
    .click();

  const consent = page.getByRole("dialog").filter({ hasText: /回收/ }).first();
  await expect(consent).toBeVisible({ timeout: 15_000 });
  // 这一条是本文件最该守的:没勾知情同意就买不到竞价
  const proceed = consent.getByRole("button", { name: /继续创建|继续|确认/ }).last();
  await expect(proceed).toBeDisabled();
  await consent.getByRole("checkbox").first().check();
  await expect(proceed).toBeEnabled();
  await proceed.click();

  // ── 列表:竞价 + 可回收标记
  await expect(page).toHaveURL(/instances/, { timeout: 20_000 });
  const row = page.locator(".ant-table-row").first();
  await expect(row.getByText("运行中")).toBeVisible({ timeout: 90_000 });
  await expect(row.getByText(/竞价/).first()).toBeVisible();
  await expect(row.getByText("可回收")).toBeVisible();

  // ── 转按量:标记消失,不再可被回收
  await row.getByRole("button", { name: /更\s*多/ }).click();
  await page.getByRole("menuitem", { name: /转按量/ }).click();
  await page
    .getByRole("button", { name: /^确\s*定$|^确认转按量$/ })
    .last()
    .click();
  await expect(page.locator(".ant-table-row").first().getByText("可回收")).toBeHidden({
    timeout: 20_000,
  });
});
