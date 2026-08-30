/**
 * 安全头与会话 cookie 标志的端到端断言(堵两类回归):
 * - API 统一安全头(SecurityHeadersMiddleware):某条中间件/代理把个头丢了当场红;
 * - refresh cookie 标志(HttpOnly/SameSite/Path)与「响应体不含 refresh_token」:
 *   收口(#13)破了 = 长期凭据重新暴露到 JS 可读面;
 * - refresh 端点的双提交头强制:缺 X-Requested-With 必须 403。
 *
 * dev 环境(Http):无 Secure、cookie 名无前缀(prod 是 __Host-superdl_refresh + Secure)。
 */
import { expect, test } from "@playwright/test";

import { registerViaUi, uniquePhone } from "./helpers";

test("API 安全头全量在", async ({ page }) => {
  // captcha-config 是免鉴权 GET,最轻;错误响应也过同一中间件链
  const resp = await page.request.get("/api/v1/auth/captcha-config");
  expect(resp.ok()).toBeTruthy();
  const h = resp.headers();
  expect(h["x-content-type-options"]).toBe("nosniff");
  expect(h["x-frame-options"]).toBe("DENY");
  expect(h["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  expect(h["content-security-policy"]).toContain("default-src 'none'");
  expect(h["permissions-policy"]).toContain("camera=()");
  expect(h["cross-origin-opener-policy"]).toBe("same-origin");
  expect(h["cross-origin-resource-policy"]).toBe("same-site");
});

test("注册/登录:refresh 只走 HttpOnly Cookie,不进响应体", async ({ page }) => {
  const phone = uniquePhone();
  await page.goto("/login");
  await page.getByText("注册", { exact: true }).click();
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/v1/auth/register")),
    (async () => {
      await page.getByPlaceholder("手机号").fill(phone);
      await page.getByRole("button", { name: "获取验证码" }).click();
      await page.getByPlaceholder("短信验证码").fill("123456");
      await page.getByRole("checkbox").check();
      await page.getByRole("button", { name: "注册并登录" }).click();
    })(),
  ]);
  expect(resp.status()).toBe(201);
  // 响应体不含 refresh_token(收口):长期凭据不出现在 JS 可读面
  const body = (await resp.json()) as Record<string, unknown>;
  expect(body.access_token).toBeTruthy();
  expect(body.refresh_token).toBeUndefined();
  // Cookie 标志:dev 是 http 无 Secure;HttpOnly + SameSite=Strict + Path=/
  const setCookie = resp.headers()["set-cookie"] ?? "";
  expect(setCookie).toContain("superdl_refresh=");
  expect(setCookie).toContain("HttpOnly");
  expect(setCookie).toContain("SameSite=strict");
  expect(setCookie).toContain("Path=/");
  expect(setCookie).not.toContain("Secure");
});

test("refresh 强制双提交头,轮换成功回写新 cookie", async ({ page }) => {
  await registerViaUi(page, uniquePhone());
  // 缺 X-Requested-With:403(CSRF 纵深)
  const bare = await page.request.post("/api/v1/auth/refresh");
  expect(bare.status()).toBe(403);
  // 带头:200,轮换并回写新 cookie,响应体仍不含 refresh_token
  const ok = await page.request.post("/api/v1/auth/refresh", {
    headers: { "X-Requested-With": "fetch" },
  });
  expect(ok.status()).toBe(200);
  expect((await ok.json()).refresh_token).toBeUndefined();
  expect(ok.headers()["set-cookie"] ?? "").toContain("superdl_refresh=");
});
