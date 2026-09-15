/** 包周期冒烟:市场页选包月 → 创建页「支付并创建」→ 余额一次性扣掉整段周期 → 列表显示「包月 · 剩 N 天」→ 续费 → 扣款回执。 */
import { expect, test } from "@playwright/test";

import { fillCustomImageForm, pickSharedStandardSku, setupUser, waitFirstRowRunning } from "./helpers";

test("买包月并续费", async ({ page }) => {
  await setupUser(page, "5000");

  await pickSharedStandardSku(page);
  await page
    .getByRole("group", { name: "计费方式" })
    .getByRole("button", { name: /^包\s*月/ })
    .click();
  await page.getByRole("button", { name: "配置实例" }).click();
  await expect(page).toHaveURL(/period=month/);

  await fillCustomImageForm(page);
  const submit = page.getByRole("button", { name: "支付并创建" });
  await expect(submit).toBeVisible({ timeout: 15_000 });
  await submit.click();

  await page.goto("/instances");
  const row = await waitFirstRowRunning(page);
  await expect(row.getByText(/包月/)).toBeVisible();
  await expect(row.getByText(/剩 \d+ 天/)).toBeVisible();

  await page.goto("/billing");
  const balance = await page
    .getByText(/¥\s*[\d,]+\.\d{2}/)
    .first()
    .innerText();
  expect(Number(balance.replace(/[^\d.]/g, ""))).toBeLessThan(5000);

  await page.goto("/instances");
  await page
    .locator("[data-row-key]")
    .first()
    .getByRole("button", { name: /更\s*多/ })
    .click();
  await page.getByRole("menuitem", { name: /^续\s*费$/ }).click();
  await expect(page.getByText("应付")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /^确认续费$/ }).click();
  await expect(page.getByText(/续费成功,本次扣款/)).toBeVisible({ timeout: 20_000 });
});
