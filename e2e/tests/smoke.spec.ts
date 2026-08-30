/**
 * 冒烟主链路:注册 → 充值(mock) → 添加 SSH 公钥 → 市场开实例(fake 编排) →
 * 运行中 → 关机(尾账) → 账单可见 → 释放(多级防护) → 列表消失。
 *
 * 前置:API(8000,已迁移+seed_dev)与 worker(outbox+reconciler)在跑;web dev server 由
 * playwright.config 自动拉起。reconciler 周期 30s,状态推进断言给足超时。
 *
 * 故意不在这层重测的:SKU 选中态 URL 持久化(market.test.tsx)、详情页直刷与事件 Tab、
 * 行展开快捷工具 —— 纯前端关注点,交给组件/路由测试,冒烟只守主链路。
 */
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

  // ── 注册
  await registerViaUi(page, phone);

  // ── 充值 100(mock 渠道)
  await rechargeViaUi(page);
  await expect(page.getByText("¥100.00").first()).toBeVisible({ timeout: 10_000 });

  // ── 添加 SSH 公钥
  await addSshKeyViaUi(page);

  // ── 市场:筛选链 + SKU 表格单选 → 结算条下一步
  await pickSharedStandardSku(page);
  await page.getByRole("button", { name: "下一步:配置实例" }).click();
  await expect(page).toHaveURL(/market\/create/);

  // ── 创建实例:自定义镜像 + 选公钥 → 创建并开机
  await fillCustomImageForm(page);
  await page.getByRole("button", { name: "创建并开机" }).click();

  // ── 实例列表:创建中 → 运行中(worker+reconciler 推进)
  const row = await waitFirstRowRunning(page);

  // ── 关机(二次确认)→ 已关机;尾账落账单
  await row.getByRole("button", { name: /^关\s*机$/ }).click();
  await page
    .getByRole("button", { name: /^关\s*机$/ })
    .last()
    .click();
  await expect(row.getByText("已关机")).toBeVisible({ timeout: 90_000 });

  await page.goto("/billing");
  await page.getByText("小时账单").click();
  await expect(page.locator(".ant-table-row").first()).toBeVisible({ timeout: 15_000 });

  // ── 释放:多级防护(键入实例名 + 勾选解锁)→ 列表消失
  await page.goto("/instances");
  await page
    .locator(".ant-table-row")
    .first()
    .getByText(/更\s*多/)
    .click();
  await page.getByText("释放实例", { exact: true }).click();
  // 多级防护两道闸:键入实例名 + 勾选清盘知情,缺一红按钮不解锁;placeholder 即实例名
  const confirmInput = page.getByLabel(/键入 .+ 以确认/);
  await confirmInput.fill((await confirmInput.getAttribute("placeholder")) ?? "");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "确认释放" }).click();
  await expect(page.getByText(/释放中|暂无|没有/).first()).toBeVisible({ timeout: 90_000 });
});
