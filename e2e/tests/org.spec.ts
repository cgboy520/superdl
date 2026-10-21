/** Organization: create, invite, accept with the disclosure, member spending, remove; organization gateway: an admin attaches a member's instance and the member detaches it (skipped without a gateway image). */

import { expect, test, type Browser, type Page } from "@playwright/test";

import { loginViaApi, setupUser, uniqueEmail } from "./helpers";

const PEER_KEY = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=";

async function memberPage(browser: Browser, email: string): Promise<{ page: Page; token: string }> {
  const page = await (await browser.newContext()).newPage();
  const token = await loginViaApi(page, email);
  return { page, token };
}

test("organization: create, invite, accept, member spending, remove", async ({ page, browser }) => {
  const stamp = Date.now();
  await loginViaApi(page, `org-owner-${stamp}@e2e.local`);
  const memberEmail = `org-member-${stamp}@e2e.local`;
  const member = await memberPage(browser, memberEmail);

  await page.goto("/org");
  await page.getByRole("button", { name: "创建组织" }).click();
  const create = page.getByRole("dialog", { name: "创建组织" });
  await create.getByLabel("组织名称").fill(`E2E Lab ${String(stamp)}`);
  await create.getByRole("button", { name: /^创\s*建$/ }).click();
  await expect(page.getByText("所有者").first()).toBeVisible();

  await page.getByRole("tab", { name: "成员", exact: true }).click();
  await page.getByRole("button", { name: "邀请成员" }).click();
  const invite = page.getByRole("dialog", { name: "邀请成员" });
  await invite.getByLabel("邮箱").fill(memberEmail);
  await invite.getByLabel("备注").fill("e2e member");
  await invite.getByRole("button", { name: "发送邀请" }).click();
  await expect(page.getByText("邀请已发出")).toBeVisible();

  await member.page.goto("/org");
  await member.page.getByRole("button", { name: "查看并接受" }).click();
  const drawer = member.page.getByRole("dialog", { name: /加入「E2E Lab/ });
  await expect(drawer.getByText("可用余额")).toBeVisible();
  await drawer.getByRole("checkbox").check();
  await drawer.getByRole("button", { name: "接受并加入" }).click();
  await expect(member.page.getByText("已加入组织")).toBeVisible();
  await expect(member.page.getByRole("tab", { name: "成员消费" })).toHaveCount(0);

  await page.getByRole("tab", { name: "成员消费" }).click();
  await expect(page.getByRole("row", { name: /e2e member/ })).toContainText("0.00");

  await page.getByRole("tab", { name: "成员", exact: true }).click();
  const row = page.getByRole("row", { name: /e2e member/ });
  await row.getByRole("button", { name: /更多/ }).click();
  await page.getByRole("menuitem", { name: "移除" }).click();
  await page
    .getByRole("button", { name: /^移\s*除$/ })
    .last()
    .click();
  await expect(page.getByText("成员已移除")).toBeVisible();

  await member.page.reload();
  await expect(member.page.getByRole("button", { name: "创建组织" })).toBeVisible();
});

test("organization gateway: an admin attaches a member's instance, the member detaches it", async ({
  page,
  browser,
}) => {
  const stamp = Date.now();
  const ownerToken = await loginViaApi(page, `orggw-owner-${stamp}@e2e.local`);
  const owner = { Authorization: `Bearer ${ownerToken}` };
  const cfg = await page.request.get("/api/v1/netgw/config", { headers: owner });
  test.skip(!((await cfg.json()) as { enabled: boolean }).enabled, "SUPERDL_NETGW_IMAGE is not set on this API");

  const memberCtxPage = await (await browser.newContext()).newPage();
  const memberToken = await setupUser(memberCtxPage, "100");
  const member = { Authorization: `Bearer ${memberToken}` };
  const skus = (await (await page.request.get("/api/v1/skus", { headers: member })).json()) as {
    id: number;
    pool_label: string;
    available_count: number;
    max_gpus_per_instance: number;
  }[];
  const sku = skus.find((s) => s.pool_label !== "kata" && s.available_count > 0);
  test.skip(!sku, "no user-namespaced SKU with stock");
  const keys = (await (await page.request.get("/api/v1/ssh-keys", { headers: member })).json()) as { id: number }[];
  const created = await page.request.post("/api/v1/instances", {
    headers: { ...member, "Idempotency-Key": uniqueEmail() },
    data: {
      sku_id: sku?.id,
      gpu_count: sku?.max_gpus_per_instance ? 1 : 0,
      name: "memberbox",
      image_ref: "registry.superdl.local/pytorch:2.9.0-cu128",
      ssh_key_ids: keys.map((k) => k.id),
    },
  });
  expect(created.status(), await created.text()).toBe(202);

  const org = await page.request.post("/api/v1/org", { headers: owner, data: { name: `GW Lab ${String(stamp)}` } });
  expect(org.status(), await org.text()).toBe(201);
  const me = (await (await page.request.get("/api/v1/me", { headers: member })).json()) as { email: string };
  const inv = await page.request.post("/api/v1/org/invitations", { headers: owner, data: { email: me.email } });
  const { uuid } = (await inv.json()) as { uuid: string };
  const disclosure = (await (await page.request.get("/api/v1/org/disclosure", { headers: member })).json()) as {
    version: string;
  };
  const accepted = await page.request.post(`/api/v1/org/invitations/received/${uuid}/accept`, {
    headers: member,
    data: { consent_version: disclosure.version },
  });
  expect(accepted.status(), await accepted.text()).toBe(200);

  await page.goto("/network?scope=org");
  await page.getByRole("button", { name: "新建组织网关" }).first().click();
  const form = page.getByRole("dialog", { name: "新建组织网关" });
  await form.getByLabel("名称", { exact: true }).fill("corp");
  const remote = form.getByLabel("隧道网段");
  await remote.fill("10.1.0.0/16");
  await remote.press("Enter");
  await form.getByLabel("网关隧道地址").fill("10.250.0.2");
  await form.getByLabel("服务器地址").fill("vpn.example.com:51820");
  await form.getByLabel("服务器公钥").fill(PEER_KEY);
  await expect(form.getByRole("switch", { name: "开启子网互通" })).toBeDisabled();
  await form.getByLabel("实例网段").fill("192.168.251.0/24");
  await form.getByRole("button", { name: "创建网关" }).click();

  const detail = page.getByRole("dialog", { name: /corp/ });
  await expect(detail.getByText("子网互通 · 已接入 0/250")).toBeVisible();
  await detail.getByRole("button", { name: "接入实例" }).click();
  const attach = page.getByRole("dialog", { name: "接入实例" });
  await attach.getByLabel("实例").click();
  await page.locator(".ant-select-item-option").filter({ hasText: "memberbox" }).click();
  await attach.getByRole("button", { name: /^接\s*入$/ }).click();
  await expect(detail.getByText("子网互通 · 已接入 1/250")).toBeVisible();

  await memberCtxPage.goto("/network");
  const mine = memberCtxPage.getByText("我在组织网关上的实例");
  await expect(mine).toBeVisible();
  await memberCtxPage
    .getByRole("row", { name: /memberbox/ })
    .getByRole("button", { name: /^移\s*除$/ })
    .click();
  await memberCtxPage.getByRole("button", { name: "确认移除" }).click();
  await expect(mine).toBeHidden();
});
