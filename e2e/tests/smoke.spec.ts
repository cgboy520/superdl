/**
 * 冒烟主链路:注册 → 充值(mock) → 添加 SSH 公钥 → 市场开实例(fake 编排) →
 * 运行中(接入按钮) → 关机(尾账) → 账单可见 → 释放(多级防护) → 列表消失。
 *
 * 前置:API(8000,已迁移+seed_dev)与 worker(outbox+reconciler)在跑;web dev server 由
 * playwright.config 自动拉起。reconciler 周期 30s,状态推进断言给足超时。
 */
import { expect, test } from "@playwright/test";

function uniquePhone(): string {
  return `139${String(Date.now()).slice(-8)}`;
}

/** 构造合法且指纹唯一的 ed25519 公钥(与后端 blob 校验一致)。 */
function genEd25519Key(): string {
  const type = "ssh-ed25519";
  const typeBytes = new TextEncoder().encode(type);
  const keyBytes = crypto.getRandomValues(new Uint8Array(32));
  const blob = new Uint8Array(4 + typeBytes.length + 4 + 32);
  const dv = new DataView(blob.buffer);
  dv.setUint32(0, typeBytes.length);
  blob.set(typeBytes, 4);
  dv.setUint32(4 + typeBytes.length, 32);
  blob.set(keyBytes, 8 + typeBytes.length);
  let bin = "";
  for (const b of blob) bin += String.fromCharCode(b);
  const b64 = btoa(bin);
  return `${type} ${b64} e2e@smoke`;
}

test("全生命周期冒烟", async ({ page }) => {
  test.setTimeout(300_000);
  const phone = uniquePhone();

  // ── 注册 ────────────────────────────────────────────────
  await page.goto("/login");
  await page.getByText("注册", { exact: true }).click();
  await page.getByPlaceholder("手机号").fill(phone);
  await page.getByRole("button", { name: "获取验证码" }).click();
  await page.getByPlaceholder("短信验证码").fill("123456");
  await page.getByRole("checkbox").check(); // 同意用户协议/隐私政策
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page).not.toHaveURL(/login/, { timeout: 15_000 });

  // ── 充值 100(mock 渠道)──────────────────────────────────
  await page.goto("/billing");
  await page.getByRole("button", { name: /^充\s*值$/ }).first().click();
  await page.getByRole("button", { name: "生成支付二维码" }).click();
  await page.getByRole("button", { name: /模拟支付成功/ }).click();
  await expect(page.getByText(/已到账/)).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press("Escape");
  await expect(page.getByText("¥100.00").first()).toBeVisible({ timeout: 10_000 });

  // ── 添加 SSH 公钥 ───────────────────────────────────────
  await page.goto("/settings");
  await page.getByLabel("名称").fill("e2e-key");
  await page.getByLabel("公钥内容").fill(genEd25519Key());
  await page.getByRole("button", { name: "添加公钥" }).click();
  await expect(page.getByText("公钥已添加")).toBeVisible({ timeout: 10_000 });

  // ── 市场:筛选链 + SKU 表格单选 → 结算条下一步 ──
  await page.goto("/market");
  const skuRow = page.locator(".ant-table-row", { hasText: "共享·标准" }).first();
  await expect(skuRow).toBeVisible({ timeout: 15_000 });
  await skuRow.getByRole("radio").check();
  await page.getByRole("button", { name: "下一步:配置实例" }).click();
  await expect(page).toHaveURL(/market\/create/);

  // ── 创建实例:自定义镜像 + 选公钥 → 创建并开机 ──────────
  await page.getByText("自定义镜像").click();
  await page
    .getByPlaceholder("registry.example.com/your/image:tag")
    .fill("registry.superdl.local/pytorch:2.9.0-cu128");
  await page.getByRole("checkbox", { name: /e2e-key/ }).check();
  const createBtn = page.getByRole("button", { name: "创建并开机" });
  await expect(createBtn).toBeEnabled({ timeout: 5_000 });
  await createBtn.click();

  // ── 实例列表:创建中 → 运行中(worker+reconciler 推进)────
  await expect(page).toHaveURL(/instances/, { timeout: 15_000 });
  const row = page.locator(".ant-table-row").first();
  await expect(row.getByText(/创建中|运行中/)).toBeVisible({ timeout: 20_000 });
  await expect(row.getByText("运行中")).toBeVisible({ timeout: 90_000 });
  await expect(row.getByRole("button", { name: "SSH" })).toBeVisible();
  await expect(row.getByText("JupyterLab")).toBeVisible();

  // ── 实例详情:双击进入 → 直刷 URL 可达 → 事件即计费依据 ──
  await row.dblclick();
  await expect(page).toHaveURL(/instances\/[0-9a-f-]{8,}/, { timeout: 10_000 });
  await page.reload();
  await expect(page.getByText("运行中").first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("tab", { name: /事\s*件/ }).click();
  await expect(page.getByText(/此事件记录即计费依据/)).toBeVisible({ timeout: 10_000 });
  await page.goto("/instances");
  await expect(page.locator(".ant-table-row").first()).toBeVisible({ timeout: 10_000 });

  // ── 关机(二次确认)→ 已关机;尾账落账单 ────────────────
  await row.getByRole("button", { name: /^关\s*机$/ }).click();
  await page.getByRole("button", { name: /^关\s*机$/ }).last().click();
  await expect(row.getByText("已关机")).toBeVisible({ timeout: 90_000 });

  await page.goto("/billing");
  await page.getByText("小时账单").click();
  await expect(page.locator(".ant-table-row").first()).toBeVisible({ timeout: 15_000 });

  // ── 释放:多级防护(勾选解锁)→ 列表消失 ────────────────
  await page.goto("/instances");
  await page.locator(".ant-table-row").first().getByText(/更\s*多/).click();
  await page.getByText("释放实例", { exact: true }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "确认释放" }).click();
  await expect(page.getByText(/释放中|暂无|没有/).first()).toBeVisible({ timeout: 90_000 });
});
