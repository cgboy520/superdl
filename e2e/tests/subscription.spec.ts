/**
 * 包周期冒烟:市场页选包月 → 创建页「支付并创建」→ 余额一次性扣掉整段周期 →
 * 列表显示「包月 · 剩 N 天」→「更多」→ 续费 → 扣款回执。前置与 smoke 同款。
 *
 * 这条挂了通常说明:计费方式 chips 没把 period 带进创建流(会开出按量实例),
 * 或结算条没换成周期总价,或续费入口/报价断了。
 */
import { expect, test } from "@playwright/test";

import {
  addSshKeyViaUi,
  pickSharedStandardSku,
  rechargeViaUi,
  registerViaUi,
  uniquePhone,
} from "./helpers";

test("买包月并续费", async ({ page }) => {
  test.setTimeout(300_000);
  const phone = uniquePhone();

  // ── 注册 + 充值(与 smoke 同款前置)
  await registerViaUi(page, phone);

  // 包月一次性预扣整段周期,默认的 ¥100 不够,显式充一个够大的数
  await rechargeViaUi(page, "5000");

  // ── SSH 公钥(开发机形态必须选一把)
  await addSshKeyViaUi(page);

  // ── 市场:选规格 → 计费方式切「包月」→ 下一步
  await pickSharedStandardSku(page);
  // 计费方式 chips 是 aria-pressed 的按钮组(ChipRow)不是 radio;antd 两字按钮会插空格,按名定位一律用正则
  await page
    .getByRole("group", { name: "计费方式" })
    .getByRole("button", { name: /^包\s*月/ })
    .click();
  await page.getByRole("button", { name: "下一步:配置实例" }).click();
  await expect(page).toHaveURL(/period=month/);

  // ── 创建页:主 CTA 是「支付」而不是「创建」
  await page.getByText("自定义镜像").click();
  await page
    .getByPlaceholder("registry.example.com/your/image:tag")
    .fill("registry.superdl.local/pytorch:2.9.0-cu128");
  await page.getByRole("checkbox", { name: /e2e-key/ }).check();
  const submit = page.getByRole("button", { name: "支付并创建" });
  await expect(submit).toBeVisible({ timeout: 15_000 });
  await submit.click();

  // ── 列表:包月标记 + 剩余天数(到期信息内联,不逐行打接口)
  await expect(page).toHaveURL(/instances/, { timeout: 15_000 });
  const row = page.locator(".ant-table-row").first();
  await expect(row.getByText(/包月/)).toBeVisible({ timeout: 90_000 });
  await expect(row.getByText(/剩 \d+ 天/)).toBeVisible();

  // ── 余额:一次性扣掉整段周期(不是一小时)
  await page.goto("/billing");
  const balance = await page.getByText(/¥\s*[\d,]+\.\d{2}/).first().innerText();
  expect(Number(balance.replace(/[^\d.]/g, ""))).toBeLessThan(5000);

  // ── 续费:更多 → 续费 → 确认 → 扣款回执
  await page.goto("/instances");
  await page.locator(".ant-table-row").first().getByRole("button", { name: /更\s*多/ }).click();
  await page.getByRole("menuitem", { name: /^续\s*费$/ }).click();
  await expect(page.getByText("续费时长")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("应付")).toBeVisible();
  await page.getByRole("button", { name: /^确认续费$/ }).click();
  await expect(page.getByText(/续费成功,本次扣款/)).toBeVisible({ timeout: 20_000 });
});
