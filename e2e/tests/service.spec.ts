/** 在线服务冒烟:在线服务页「部署服务」→ 部署页(页内选规格)→ 容器与服务配置 → 部署 → 服务详情 → 运行中 → 端点卡拿到 URL → 更新版本(v2)→ 新建 API Key(一次性展示)→ 吊销 → 停止服务 → 设置里改名与关鉴权。 */
import { expect, test } from "@playwright/test";

import { setupUser } from "./helpers";

test("部署在线服务并拿到端点与 API Key", async ({ page }) => {
  // 建号 + 充值 + 公钥(API 直达)
  await setupUser(page, "500");

  // 在线服务页「部署服务」直达部署页;规格在页内选
  await page.goto("/services");
  await page.getByRole("button", { name: "部署服务" }).first().click();
  await expect(page).toHaveURL(/\/services\/new$/);
  const skuRow = page.locator(".ant-table-row", { hasText: "共享·标准" }).first();
  await expect(skuRow).toBeVisible({ timeout: 15_000 });
  await skuRow.getByRole("radio").check();
  await expect(page.getByText("GPU 数量")).toBeVisible();

  // 容器配置:可变 tag 必须被拦住
  const image = page.getByLabel("镜像地址");
  await image.fill("registry.superdl.local/vllm:latest");
  await expect(page.getByRole("button", { name: "部署服务" })).toBeDisabled();
  await image.fill("registry.superdl.local/vllm:v0.6.3");

  // 服务配置:端口 + 健康检查(不开 SSH)
  await page.getByLabel("服务端口").fill("8000");
  await page.getByLabel("健康检查").fill("/health");
  await page.getByRole("button", { name: "部署服务" }).click();

  // 部署后直达服务详情:部署中 → 运行中
  await expect(page).toHaveURL(/\/services\/svc-[a-z0-9]+/, { timeout: 20_000 });
  await expect(page.getByText("运行中").first()).toBeVisible({ timeout: 90_000 });
  // 端点卡:完整 URL 的 <code>(取端点卡那个,不取概览 Tab 的 curl 示例)
  await expect(page.locator("code", { hasText: /^https:\/\/svc-[a-z0-9]+\./ }).first()).toBeVisible();

  // 更新版本:抽屉里换镜像 → 发布 → 头部翻成 v2 → 回到运行中
  await page.getByRole("button", { name: "更新版本" }).click();
  const drawer = page.getByRole("dialog");
  await drawer.getByLabel("镜像地址").fill("registry.superdl.local/vllm:v0.7.0");
  await drawer.getByRole("button", { name: "发布新版本" }).click();
  await page
    .locator(".ant-modal-confirm-btns")
    .getByRole("button", { name: /^确\s*定$/ })
    .click();
  await expect(page.getByText("v2", { exact: true }).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("运行中").first()).toBeVisible({ timeout: 90_000 });
  // 版本与事件合成「历史」Tab
  await page.getByRole("tab", { name: "历史" }).click();
  await expect(page.locator("tbody").getByText("v2")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("tbody").getByText("当前")).toBeVisible();

  // 访问密钥 Tab:新建 Key 一次性展示,勾选前关不掉
  await page.getByRole("tab", { name: "访问密钥" }).click();
  await page.getByRole("button", { name: "新建 Key" }).click();
  await page.getByLabel("名称").fill("e2e");
  // antd 两字按钮会插空格,按名定位用正则
  await page.getByRole("button", { name: /^创\s*建$/ }).click();
  await expect(page.getByText("关闭后无法再查看")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/^sk-[A-Za-z0-9_-]{8,}$/)).toBeVisible();
  // 禁用闸由 ApiKeyModal.test.tsx 覆盖,这层只走通流程
  const closeBtn = page.getByRole("button", { name: "已保存,关闭" });
  await page.getByRole("checkbox", { name: "我已保存这把 Key" }).check();
  await closeBtn.click();
  await expect(page.getByText("关闭后无法再查看")).toBeHidden({ timeout: 10_000 });
  await expect(page.locator("tbody").getByText("e2e")).toBeVisible({ timeout: 15_000 });

  await page
    .getByRole("button", { name: /^吊\s*销$/ })
    .first()
    .click();
  await page
    .locator(".ant-modal-confirm-btns")
    .getByRole("button", { name: /^确\s*定$/ })
    .click();
  await expect(page.getByText("已吊销").first()).toBeVisible({ timeout: 15_000 });

  // 停止服务:头部按钮 → 确认 → 状态离开运行中;端点与 Key 仍在
  await page.getByRole("button", { name: /^停\s*止$/ }).first().click();
  await page
    .locator(".ant-modal-confirm-btns")
    .getByRole("button", { name: /^确\s*定$/ })
    .click();
  await expect(page.getByText(/停止中|已停止/).first()).toBeVisible({ timeout: 30_000 });

  // 设置 Tab:改名反映到头部;关鉴权走确认,端点卡显示「公开访问」
  await page.getByRole("tab", { name: "设置" }).click();
  await page.getByLabel("服务名称").fill("e2e-renamed");
  await page.getByRole("button", { name: /^保\s*存$/ }).click();
  await expect(page.getByRole("heading", { name: "e2e-renamed" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("switch", { name: "访问鉴权" }).click();
  await page
    .locator(".ant-modal-confirm-btns")
    .getByRole("button", { name: /^确\s*定$/ })
    .click();
  await expect(page.getByText("公开访问").first()).toBeVisible({ timeout: 15_000 });

  // 列表:服务在「在线服务」里,不在容器实例里
  await page.goto("/services");
  // 行里有两处 slug 文本,取名称列精确匹配
  await expect(page.locator(".ant-table-row").first().getByText(/^svc-[a-z0-9]+$/)).toBeVisible({
    timeout: 15_000,
  });
  await page.goto("/instances");
  await expect(page.locator(".ant-table-row")).toHaveCount(0);
});
