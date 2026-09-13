/** 冒烟主链路:注册 → 充值(mock)→ 添加 SSH 公钥 → 市场开实例(fake 编排)→ 落到实例详情「连接」Tab → 列表运行中 → 关机(尾账)→ 账单可见 → 释放(多级防护)→ 列表消失。前置:API(8000,已迁移 + seed_dev)与 worker 在跑;web dev server 由 playwright.config 拉起;reconciler 周期 30s。纯前端关注点(URL 持久化、详情页)交给组件/路由测试。 */
import { expect, test } from "@playwright/test";

import {
  addSshKeyViaUi,
  fillCustomImageForm,
  pickSharedStandardSku,
  rechargeViaUi,
  registerViaUi,
  uniquePhone,
  waitFirstRowRunning,
} from "./helpers";

test("全生命周期冒烟", async ({ page }) => {
  const phone = uniquePhone();

  await registerViaUi(page, phone);

  // 充值 100(mock 渠道)
  await rechargeViaUi(page);
  await expect(page.getByText("¥100.00").first()).toBeVisible({ timeout: 10_000 });

  await addSshKeyViaUi(page);

  // 市场:筛选链 + SKU 表格单选 → 结算条下一步
  await pickSharedStandardSku(page);
  await page.getByRole("button", { name: "配置实例" }).click();
  await expect(page).toHaveURL(/market\/create/);

  // 创建实例:自定义镜像 + 选公钥(唯一一把已自动选中)→ 创建并开机 → 直达详情「连接」Tab
  await fillCustomImageForm(page);
  await page.getByRole("button", { name: "创建并开机" }).click();
  await expect(page).toHaveURL(/\/instances\/[0-9a-f]{32}\?tab=access/, { timeout: 20_000 });

  // 实例列表:创建中 → 运行中
  await page.goto("/instances");
  const row = await waitFirstRowRunning(page);

  // 关机(二次确认)→ 已关机;尾账落账单
  await row.getByRole("button", { name: /^关\s*机$/ }).click();
  await page
    .getByRole("button", { name: /^关\s*机$/ })
    .last()
    .click();
  await expect(row.getByText("已关机")).toBeVisible({ timeout: 90_000 });

  await page.goto("/billing");
  await page.getByRole("tab", { name: "小时账单" }).click();
  await expect(page.locator(".ant-table-row").first()).toBeVisible({ timeout: 15_000 });

  // 释放:多级防护(键入实例名 + 勾选解锁)→ 列表消失
  await page.goto("/instances");
  await page
    .locator(".ant-table-row")
    .first()
    .getByText(/更\s*多/)
    .click();
  await page.getByText("释放实例", { exact: true }).click();
  // 两道闸缺一红按钮不解锁;placeholder 即实例名
  const confirmInput = page.getByLabel(/键入 .+ 以确认/);
  await confirmInput.fill((await confirmInput.getAttribute("placeholder")) ?? "");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "确认释放" }).click();
  await expect(page.getByText(/释放中|暂无|没有/).first()).toBeVisible({ timeout: 90_000 });
});
