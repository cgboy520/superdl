import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setupAuthCacheGuard } from "./authCacheGuard";
import { authStore } from "../stores/auth";

/** 指定 sub 的伪 JWT(只塑形不签名:守卫只解 payload 不验签)。 */
function fakeJwt(sub: string): string {
  const b64 = (o: unknown) =>
    btoa(JSON.stringify(o)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub })}.sig`;
}

describe("setupAuthCacheGuard", () => {
  beforeEach(() => {
    localStorage.clear();
    authStore.setState({ accessToken: null });
  });
  afterEach(() => {
    localStorage.clear();
    authStore.setState({ accessToken: null });
  });

  it("登出(token → null)清空查询缓存", async () => {
    const qc = new QueryClient();
    const clear = vi.spyOn(qc, "clear");
    const un = setupAuthCacheGuard(qc);
    authStore.getState().login(fakeJwt("1"));
    authStore.getState().logout();
    await vi.waitFor(() => expect(clear).toHaveBeenCalledTimes(1));
    un();
  });

  it("静默续期(同账号 token A→A')不清缓存:续期每小时一次,清了全站每小时白屏", async () => {
    const qc = new QueryClient();
    const clear = vi.spyOn(qc, "clear");
    const un = setupAuthCacheGuard(qc);
    authStore.getState().login(fakeJwt("1"));
    authStore.getState().login(fakeJwt("1")); // 续期路径直接 login 新 access token(sub 不变)
    await new Promise((r) => setTimeout(r, 20));
    expect(clear).not.toHaveBeenCalled();
    un();
  });

  it("换号登录(sub A→B)清缓存:否则 B 先看到 A 的余额与实例(跨账号泄漏)", async () => {
    const qc = new QueryClient();
    const clear = vi.spyOn(qc, "clear");
    const un = setupAuthCacheGuard(qc);
    authStore.getState().login(fakeJwt("1"));
    authStore.getState().login(fakeJwt("2")); // 会话过期后另一账号直接登录(无显式登出)
    await vi.waitFor(() => expect(clear).toHaveBeenCalledTimes(1));
    un();
  });

  it("token 不可解码且值不同按换号处理(拿不准就清,安全方向)", async () => {
    const qc = new QueryClient();
    const clear = vi.spyOn(qc, "clear");
    const un = setupAuthCacheGuard(qc);
    authStore.getState().login("not-a-jwt-a");
    authStore.getState().login("not-a-jwt-b");
    await vi.waitFor(() => expect(clear).toHaveBeenCalledTimes(1));
    un();
  });
});
