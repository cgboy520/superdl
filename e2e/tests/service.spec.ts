/** 在线服务的部署、版本更新、访问密钥、停止、头部改名与鉴权设置冒烟。 */
import { expect, test } from "@playwright/test";

import { confirmOk, setupUser } from "./helpers";

test("部署在线服务并拿到端点与 API Key", async ({ page }) => {
  await setupUser(page, "500");

  await page.goto("/services");
  await page.getByRole("button", { name: "部署服务" }).first().click();
  await expect(page).toHaveURL(/\/services\/new$/);
  const skuRow = page.locator("[data-row-key]", { hasText: "共享·标准" }).first();
  await expect(skuRow).toBeVisible({ timeout: 15_000 });
  await skuRow.getByRole("radio").check();
  await expect(page.getByText("GPU 数量")).toBeVisible();

  const image = page.getByLabel("镜像地址");
  await image.fill("registry.superdl.local/vllm:latest");
  await expect(page.getByRole("button", { name: "部署服务" })).toBeDisabled();
  await image.fill("registry.superdl.local/vllm:v0.6.3");

  await page.getByLabel("服务端口").fill("8000");
  await page.getByLabel("健康检查").fill("/health");
  await page.getByRole("button", { name: "部署服务" }).click();

  await expect(page).toHaveURL(/\/services\/svc-[a-z0-9]+/, { timeout: 20_000 });
  await expect(page.getByText("运行中").first()).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId("endpoint-url")).toHaveText(/^https:\/\/svc-[a-z0-9]+\./);

  await page.getByRole("button", { name: "更新版本" }).click();
  const drawer = page.getByRole("dialog");
  await drawer.getByLabel("镜像地址").fill("registry.superdl.local/vllm:v0.7.0");
  await drawer.getByRole("button", { name: "发布新版本" }).click();
  await confirmOk(page);
  await expect(page.getByText("v2", { exact: true }).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("运行中").first()).toBeVisible({ timeout: 90_000 });
  await page.getByRole("tab", { name: "历史" }).click();
  await expect(page.locator("tbody").getByText("v2")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("tbody").getByText("当前")).toBeVisible();

  await page.getByRole("tab", { name: "访问密钥" }).click();
  await page.getByRole("button", { name: "新建 Key" }).click();
  await page.getByLabel("名称").fill("e2e");
  await page.getByRole("button", { name: /^创\s*建$/ }).click();
  await expect(page.getByText("关闭后无法再查看")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/^sk-[A-Za-z0-9_-]{8,}$/)).toBeVisible();
  const closeBtn = page.getByRole("button", { name: "已保存,关闭" });
  await page.getByRole("checkbox", { name: "我已保存这把 Key" }).check();
  await closeBtn.click();
  await expect(page.getByText("关闭后无法再查看")).toBeHidden({ timeout: 10_000 });
  await expect(page.locator("tbody").getByText("e2e")).toBeVisible({ timeout: 15_000 });

  await page
    .getByRole("button", { name: /^吊\s*销$/ })
    .first()
    .click();
  await confirmOk(page);
  await expect(page.getByText("已吊销").first()).toBeVisible({ timeout: 15_000 });

  await page
    .getByRole("button", { name: /^停\s*止$/ })
    .first()
    .click();
  await confirmOk(page);
  await expect(page.getByText(/停止中|已停止/).first()).toBeVisible({ timeout: 30_000 });

  await page.getByRole("tab", { name: "设置" }).click();
  await page.getByRole("button", { name: /^改名:/ }).click();
  const nameEdit = page.getByLabel(/^改名:/);
  await nameEdit.fill("e2e-renamed");
  await nameEdit.press("Enter");
  await expect(page.getByRole("heading", { name: "e2e-renamed" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("switch", { name: "访问鉴权" }).click();
  await confirmOk(page);
  await expect(page.getByText("公开访问").first()).toBeVisible({ timeout: 15_000 });

  await page.goto("/services");
  await expect(
    page
      .locator("[data-row-key]")
      .first()
      .getByText(/^svc-[a-z0-9]+$/),
  ).toBeVisible({
    timeout: 15_000,
  });
  await page.goto("/instances");
  await expect(page.locator("[data-row-key]")).toHaveCount(0);
});
