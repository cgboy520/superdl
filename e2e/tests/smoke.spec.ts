/**
 * 冒烟主链路:注册 → 充值(mock) → 添加 SSH 公钥 → 市场开实例(fake 编排) →
 * 运行中(接入按钮) → 关机(尾账) → 账单可见 → 释放(多级防护) → 列表消失。
 *
 * 前置:API(8000,已迁移+seed_dev)与 worker(outbox+reconciler)在跑;web dev server 由
 * playwright.config 自动拉起。reconciler 周期 30s,状态推进断言给足超时。
 */
import { expect, test } from "@playwright/test";

import {
  addSshKeyViaUi,
  pickSharedStandardSku,
  rechargeViaUi,
  registerViaUi,
  uniquePhone,
} from "./helpers";

test("全生命周期冒烟", async ({ page }) => {
  test.setTimeout(300_000);
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
  await page.getByText("自定义镜像").click();
  await page
    .getByPlaceholder("registry.example.com/your/image:tag")
    .fill("registry.superdl.local/pytorch:2.9.0-cu128");
  await page.getByRole("checkbox", { name: /e2e-key/ }).check();
  await page.getByRole("button", { name: "创建并开机" }).click();

  // ── 实例列表:创建中 → 运行中(worker+reconciler 推进)
  await expect(page).toHaveURL(/instances/, { timeout: 15_000 });
  const row = page.locator(".ant-table-row").first();
  await expect(row.getByText("运行中")).toBeVisible({ timeout: 90_000 });
  // 快捷工具在行展开区(antd 渲染成兄弟 tr.ant-table-expanded-row),access 也是展开才拉,须先点开再断言
  await row.locator(".ant-table-row-expand-icon").click();
  const tools = page.locator(".ant-table-expanded-row").first();
  await expect(tools.getByRole("button", { name: "SSH" })).toBeVisible({ timeout: 15_000 });
  await expect(tools.getByText("JupyterLab")).toBeVisible();

  // ── 实例详情:双击进入 → 直刷 URL 可达 → 事件即计费依据
  await row.dblclick();
  await expect(page).toHaveURL(/instances\/[0-9a-f-]{8,}/, { timeout: 10_000 });
  await page.reload();
  await expect(page.getByText("运行中").first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("tab", { name: /事\s*件/ }).click();
  await expect(page.getByText(/此事件记录即计费依据/)).toBeVisible({ timeout: 10_000 });
  await page.goto("/instances");

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
  // 多级防护两道闸(ui-ux-spec 规则 4):键入实例名 + 勾选清盘知情,缺一红按钮不解锁;placeholder 即实例名
  const confirmInput = page.getByLabel(/请输入实例名/);
  await confirmInput.fill((await confirmInput.getAttribute("placeholder")) ?? "");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "确认释放" }).click();
  await expect(page.getByText(/释放中|暂无|没有/).first()).toBeVisible({ timeout: 90_000 });
});
