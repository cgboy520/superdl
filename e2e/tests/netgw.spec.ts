/** Private network: create a WireGuard gateway, read the public key, add and delete a forward, delete the gateway. Skipped when the API has no gateway image configured. */

import { expect, test } from "@playwright/test";

import { loginViaApi } from "./helpers";

const PEER_KEY = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=";

test("private network gateway lifecycle", async ({ page }) => {
  const token = await loginViaApi(page, `netgw-${Date.now()}@e2e.local`);
  const cfg = await page.request.get("/api/v1/netgw/config", { headers: { Authorization: `Bearer ${token}` } });
  test.skip(!((await cfg.json()) as { enabled: boolean }).enabled, "SUPERDL_NETGW_IMAGE is not set on this API");

  await page.goto("/network");
  await expect(page.getByText("203.0.113.7").first()).toBeVisible();
  await page.getByRole("button", { name: "新建网关" }).first().click();

  const form = page.getByRole("dialog", { name: "新建私网网关" });
  await form.getByLabel("名称", { exact: true }).fill("corp");
  const remote = form.getByLabel("隧道网段");
  await remote.fill("10.1.0.0/16");
  await remote.press("Enter");
  await form.getByLabel("网关隧道地址").fill("10.250.0.2");
  await form.getByLabel("服务器地址").fill("vpn.example.com:51820");
  await form.getByLabel("服务器公钥").fill(PEER_KEY);
  await form.getByRole("button", { name: "创建网关" }).click();

  const detail = page.getByRole("dialog", { name: /corp/ });
  await expect(detail.getByText("网关公钥")).toBeVisible();
  await expect(detail.getByText("export ALL_PROXY=socks5h://corp:1080")).toBeVisible();

  await detail.getByRole("button", { name: "添加映射" }).click();
  const add = page.getByRole("dialog", { name: "添加映射" });
  await add.getByLabel("名称", { exact: true }).fill("nas");
  await add.getByLabel("端口", { exact: true }).fill("2049");
  await add.getByLabel("内网目标").fill("10.1.0.5");
  await add.getByLabel("目标端口").fill("2049");
  await add.getByRole("button", { name: "添加映射" }).click();
  await expect(detail.getByText("nas:2049")).toBeVisible();

  await detail.getByRole("button", { name: /^删\s*除$/ }).click();
  await page.getByRole("button", { name: "删除映射" }).click();
  await expect(detail.getByText("暂无映射")).toBeVisible();
  await expect(page.getByRole("dialog", { name: /删除映射/ })).toBeHidden();
  await detail.getByRole("button", { name: "关闭" }).click();
  await expect(detail).toBeHidden();

  await page.getByRole("row", { name: /corp/ }).getByRole("button", { name: /更多/ }).click();
  await page.getByRole("menuitem", { name: "删除" }).click();
  const confirm = page.getByRole("dialog", { name: "删除私网网关" });
  await confirm.getByRole("textbox").fill("corp");
  await confirm.getByRole("button", { name: "确认删除" }).click();
  await expect(page.getByRole("row", { name: /corp/ })).toContainText("删除中");
});
