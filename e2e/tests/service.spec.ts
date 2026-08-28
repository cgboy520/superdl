/**
 * 服务容器冒烟:市场「部署服务」→ 填容器与对外服务 → 部署 → 运行中 →
 * 「服务」Tab 拿到端点 URL → 新建 API Key(一次性展示)→ 吊销。
 * 前置与 smoke 同款:API+worker 在跑,fake 编排。
 *
 * 这条挂了通常说明:创建页的形态分叉断了(服务字段没提交/dev 字段漏传),
 * 或「服务」Tab 没按 workload_type 渲染,或一次性 Key 弹窗的保存闸松了。
 */
import { expect, test } from "@playwright/test";

import { pickSharedStandardSku, rechargeViaUi, registerViaUi, uniquePhone } from "./helpers";

test("部署服务并拿到端点与 API Key", async ({ page }) => {
  test.setTimeout(300_000);
  const phone = uniquePhone();

  // ── 注册 + 充值(与 smoke 同款前置)
  await registerViaUi(page, phone);
  await rechargeViaUi(page);

  // ── 市场:选规格 → 结算条「部署服务」进服务形态创建流
  await pickSharedStandardSku(page);
  await page.getByRole("button", { name: "部署服务" }).click();
  await expect(page).toHaveURL(/workload=service/);

  // ── 容器卡:可变 tag 必须被拦住(后端也有硬闸,这里守前端早报错)
  const image = page.getByLabel("镜像地址");
  await image.fill("registry.superdl.local/vllm:latest");
  await expect(page.getByRole("button", { name: "部署服务" })).toBeDisabled();
  await image.fill("registry.superdl.local/vllm:v0.6.3");

  // ── 对外服务卡:端口 + 健康检查(不开 SSH,因此不用选公钥)
  await page.getByLabel("服务端口").fill("8000");
  await page.getByLabel("健康检查").fill("/health");
  await page.getByRole("button", { name: "部署服务" }).click();

  // ── 列表:创建中 → 运行中
  await expect(page).toHaveURL(/instances/, { timeout: 15_000 });
  const row = page.locator(".ant-table-row").first();
  await expect(row.getByText("运行中")).toBeVisible({ timeout: 90_000 });
  // 服务型实例在列表里带标记,slug 随 InstanceOut 一起下发(不额外打接口)
  await expect(row.getByText(/svc-[a-z0-9]+/)).toBeVisible();

  // ── 详情「服务」Tab:端点 URL
  await row.dblclick();
  await expect(page).toHaveURL(/instances\/[0-9a-f-]{8,}/, { timeout: 10_000 });
  await page.getByRole("tab", { name: "服务" }).click();
  await expect(page.getByText("服务端点")).toBeVisible({ timeout: 15_000 });
  // 端点 URL 在页面上出现两次(端点卡 + curl 示例),取卡片里那个 <code>
  await expect(page.locator("code", { hasText: /^https:\/\/svc-[a-z0-9]+\./ })).toBeVisible();

  // ── 新建 Key:一次性展示,勾选前关不掉
  await page.getByRole("button", { name: "新建 Key" }).click();
  await page.getByLabel("名称").fill("e2e");
  // antd 会在两个汉字之间自动插空格(「创 建」),按名定位一律用正则容忍它
  await page.getByRole("button", { name: /^创\s*建$/ }).click();
  await expect(page.getByText("关闭后无法再查看")).toBeVisible({ timeout: 15_000 });
  const plainKey = page.getByText(/^sk-[A-Za-z0-9_-]{8,}$/);
  await expect(plainKey).toBeVisible();
  // 这一条是本文件最该守的:没勾「我已保存」就关不掉窗
  const closeBtn = page.getByRole("button", { name: "已保存,关闭" });
  await expect(closeBtn).toBeDisabled();
  await page.getByRole("checkbox", { name: "我已保存这把 Key" }).check();
  await expect(closeBtn).toBeEnabled();
  await closeBtn.click();

  // 关窗后明文再也不出现在页面上(列表只回前缀)
  await expect(page.getByText("关闭后无法再查看")).toBeHidden({ timeout: 10_000 });
  await expect(page.locator("tbody").getByText("e2e")).toBeVisible({ timeout: 15_000 });

  // ── 吊销
  await page
    .getByRole("button", { name: /^吊\s*销$/ })
    .first()
    .click();
  // 确认弹窗用的是 antd 默认 okText(「确 定」)而非行内的「吊销」,按后者取 .last() 会拿到被遮罩挡住的按钮
  await page
    .locator(".ant-modal-confirm-btns")
    .getByRole("button", { name: /^确\s*定$/ })
    .click();
  await expect(page.getByText("已吊销").first()).toBeVisible({ timeout: 15_000 });
});
