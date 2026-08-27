/**
 * 服务容器冒烟:市场「部署服务」→ 填容器与对外服务 → 部署 → 运行中 →
 * 「服务」Tab 拿到端点 URL → 新建 API Key(一次性展示)→ 吊销。
 *
 * 与 smoke.spec.ts 分开一个文件:它跑的是开发机形态的全生命周期,这条跑服务形态的
 * 独有链路(端点、Key、一次性展示),两者共用同一套前置(API+worker 在跑,fake 编排)。
 *
 * 这条挂了通常说明:创建页的形态分叉断了(服务字段没提交/dev 字段漏传),
 * 或「服务」Tab 没按 workload_type 渲染,或一次性 Key 弹窗的保存闸松了 ——
 * 最后那条最要命:用户会在没抄下 Key 的情况下关窗,而 Key 再也取不回来。
 */
import { expect, test } from "@playwright/test";

import { uniquePhone } from "./helpers";

test("部署服务并拿到端点与 API Key", async ({ page }) => {
  test.setTimeout(300_000);
  const phone = uniquePhone();

  // ── 注册 + 充值(与 smoke 同款前置)──────────────────────
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

  // ── 市场:选规格 → 结算条「部署服务」进服务形态创建流 ────
  await page.goto("/market");
  const skuRow = page.locator(".ant-table-row", { hasText: "共享·标准" }).first();
  await expect(skuRow).toBeVisible({ timeout: 15_000 });
  await skuRow.getByRole("radio").check();
  await page.getByRole("button", { name: "部署服务" }).click();
  await expect(page).toHaveURL(/workload=service/);

  // ── 容器卡:可变 tag 必须被拦住(后端也有硬闸,这里守前端早报错)──
  const image = page.getByLabel("镜像地址");
  await image.fill("registry.superdl.local/vllm:latest");
  await expect(page.getByRole("button", { name: "部署服务" })).toBeDisabled();
  await image.fill("registry.superdl.local/vllm:v0.6.3");

  // ── 对外服务卡:端口 + 健康检查(不开 SSH,因此不用选公钥)──
  await page.getByLabel("服务端口").fill("8000");
  await page.getByLabel("健康检查").fill("/health");
  await page.getByRole("button", { name: "部署服务" }).click();

  // ── 列表:创建中 → 运行中 ─────────────────────────────────
  await expect(page).toHaveURL(/instances/, { timeout: 15_000 });
  const row = page.locator(".ant-table-row").first();
  await expect(row.getByText("运行中")).toBeVisible({ timeout: 90_000 });
  // 服务型实例在列表里带标记,slug 随 InstanceOut 一起下发(不额外打接口)
  await expect(row.getByText(/ep-[a-z0-9]+/)).toBeVisible();

  // ── 详情「服务」Tab:端点 URL ────────────────────────────
  await row.dblclick();
  await expect(page).toHaveURL(/instances\/[0-9a-f-]{8,}/, { timeout: 10_000 });
  await page.getByRole("tab", { name: "服务" }).click();
  await expect(page.getByText("服务端点")).toBeVisible({ timeout: 15_000 });
  // 端点 URL 在页面上出现两次(端点卡 + curl 示例),取卡片里那个 <code>
  await expect(page.locator("code", { hasText: /^https:\/\/ep-[a-z0-9]+\./ })).toBeVisible();

  // ── 新建 Key:一次性展示,勾选前关不掉 ────────────────────
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

  // ── 吊销 ──────────────────────────────────────────────────
  await page
    .getByRole("button", { name: /^吊\s*销$/ })
    .first()
    .click();
  // 确认弹窗用的是 antd 默认 okText(「确 定」),不是行内那个「吊销」——
  // 按 /^吊\s*销$/ 取 .last() 只会拿到被遮罩挡住的行内按钮,然后一直点不动
  await page
    .locator(".ant-modal-confirm-btns")
    .getByRole("button", { name: /^确\s*定$/ })
    .click();
  await expect(page.getByText("已吊销").first()).toBeVisible({ timeout: 15_000 });
});
