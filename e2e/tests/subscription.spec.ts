/** 包周期冒烟:市场页选包月 → 创建页「支付并创建」→ 余额一次性扣掉整段周期 → 列表显示「包月 · 剩 N 天」→ 续费 → 扣款回执。 */
import { expect, test } from "@playwright/test";

import {
  fillCustomImageForm,
  pickSharedStandardSku,
  setupUser,
  waitFirstRowRunning,
} from "./helpers";

test("买包月并续费", async ({ page }) => {
  // 建号 + 充值 + 公钥(API 直达)
  // 包月一次性预扣整段周期,充大额
  await setupUser(page, "5000");

  // 市场:选规格 → 计费方式切「包月」→ 下一步
  await pickSharedStandardSku(page);
  // 计费方式 chips 是 aria-pressed 按钮组;antd 两字按钮会插空格,按名定位用正则
  await page
    .getByRole("group", { name: "计费方式" })
    .getByRole("button", { name: /^包\s*月/ })
    .click();
  await page.getByRole("button", { name: "下一步:配置实例" }).click();
  await expect(page).toHaveURL(/period=month/);

  // 创建页:主 CTA 是「支付」
  await fillCustomImageForm(page);
  const submit = page.getByRole("button", { name: "支付并创建" });
  await expect(submit).toBeVisible({ timeout: 15_000 });
  await submit.click();

  // 列表:包月标记 + 剩余天数
  const row = await waitFirstRowRunning(page);
  await expect(row.getByText(/包月/)).toBeVisible();
  await expect(row.getByText(/剩 \d+ 天/)).toBeVisible();

  // 余额:一次性扣掉整段周期
  await page.goto("/billing");
  const balance = await page.getByText(/¥\s*[\d,]+\.\d{2}/).first().innerText();
  expect(Number(balance.replace(/[^\d.]/g, ""))).toBeLessThan(5000);

  // 续费:更多 → 续费 → 确认 → 扣款回执
  await page.goto("/instances");
  await page.locator(".ant-table-row").first().getByRole("button", { name: /更\s*多/ }).click();
  await page.getByRole("menuitem", { name: /^续\s*费$/ }).click();
  await expect(page.getByText("应付")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /^确认续费$/ }).click();
  await expect(page.getByText(/续费成功,本次扣款/)).toBeVisible({ timeout: 20_000 });
});
