/** 注册、mock 充值、添加公钥、创建实例、关机、查看账单与释放的浏览器冒烟。 */
import { expect, test } from "@playwright/test";

import {
  addSshKeyViaUi,
  fillCustomImageForm,
  moneyText,
  pickSharedStandardSku,
  rechargeViaUi,
  registerViaUi,
  uniqueEmail,
  waitFirstRowRunning,
} from "./helpers";

test("全生命周期冒烟", async ({ page }) => {
  const email = uniqueEmail();

  await registerViaUi(page, email);

  await rechargeViaUi(page);
  await expect(page.getByText(await moneyText(page, "100.00")).first()).toBeVisible({ timeout: 10_000 });

  await addSshKeyViaUi(page);

  await pickSharedStandardSku(page);
  await page.getByRole("button", { name: "配置实例" }).click();
  await expect(page).toHaveURL(/market\/create/);

  await fillCustomImageForm(page);
  await page.getByRole("button", { name: "创建并开机" }).click();
  await expect(page).toHaveURL(/\/instances\/[0-9a-f]{32}\?tab=access/, { timeout: 20_000 });

  await page.goto("/instances");
  const row = await waitFirstRowRunning(page);

  await row.getByRole("button", { name: /^关\s*机$/ }).click();
  await page
    .getByRole("button", { name: /^关\s*机$/ })
    .last()
    .click();
  await expect(row.getByText("已关机")).toBeVisible({ timeout: 90_000 });

  await page.goto("/billing");
  await page.getByRole("tab", { name: "小时账单" }).click();
  await expect(page.locator("[data-row-key]").first()).toBeVisible({ timeout: 15_000 });

  await page.goto("/instances");
  await page
    .locator("[data-row-key]")
    .first()
    .getByText(/更\s*多/)
    .click();
  await page.getByText("释放实例", { exact: true }).click();
  const confirmInput = page.getByLabel(/键入 .+ 以确认/);
  await confirmInput.fill((await confirmInput.getAttribute("placeholder")) ?? "");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "确认释放" }).click();
  await expect(page.getByText(/释放中|暂无|没有/).first()).toBeVisible({ timeout: 90_000 });
});
