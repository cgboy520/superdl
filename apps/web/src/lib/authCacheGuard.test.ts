import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setupAuthCacheGuard } from "./authCacheGuard";
import { authStore } from "../stores/auth";

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
    authStore.getState().login("tok-a");
    authStore.getState().logout();
    await vi.waitFor(() => expect(clear).toHaveBeenCalledTimes(1));
    un();
  });

  it("静默续期(token A→A')不清缓存:续期每小时一次,清了全站每小时白屏", async () => {
    const qc = new QueryClient();
    const clear = vi.spyOn(qc, "clear");
    const un = setupAuthCacheGuard(qc);
    authStore.getState().login("tok-a");
    authStore.getState().login("tok-b"); // 续期路径直接 login 新 access token
    await new Promise((r) => setTimeout(r, 20));
    expect(clear).not.toHaveBeenCalled();
    un();
  });
});
