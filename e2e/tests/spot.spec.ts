/** 竞价实例的折后价、知情同意、可回收标记与转按量冒烟。 */
import { expect, test } from "@playwright/test";

import { fillCustomImageForm, pickSharedStandardSku, setupUser, waitFirstRowRunning } from "./helpers";

test("买竞价并转按量", async ({ page }) => {
  await setupUser(page, "500");

  await pickSharedStandardSku(page);
  await page
    .getByRole("group", { name: "计费方式" })
    .getByRole("button", { name: /^竞\s*价/ })
    .click();
  await expect(page.getByText(/可被平台回收|可被回收/).first()).toBeVisible();
  await page.getByRole("button", { name: "配置实例" }).click();
  await expect(page).toHaveURL(/market=spot/);

  await fillCustomImageForm(page);
  await page
    .getByRole("button", { name: /创建并开机|支付并创建/ })
    .first()
    .click();

  const consent = page.getByRole("dialog").filter({ hasText: /回收/ }).first();
  await expect(consent).toBeVisible({ timeout: 15_000 });
  const proceed = consent.getByRole("button", { name: /继续创建|继续|确认/ }).last();
  await expect(proceed).toBeDisabled();
  await consent.getByRole("checkbox").first().check();
  await expect(proceed).toBeEnabled();
  await proceed.click();

  await page.goto("/instances");
  const row = await waitFirstRowRunning(page);
  await expect(row.getByText(/竞价/).first()).toBeVisible();
  await expect(row.getByText("可回收")).toBeVisible();

  await row.getByRole("button", { name: /更\s*多/ }).click();
  await page.getByRole("menuitem", { name: /转按量/ }).click();
  await page
    .getByRole("button", { name: /^确\s*定$|^确认转按量$/ })
    .last()
    .click();
  await expect(page.locator("[data-row-key]").first().getByText("可回收")).toBeHidden({
    timeout: 20_000,
  });
});
