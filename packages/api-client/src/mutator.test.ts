import { beforeEach, describe, expect, it, vi } from "vitest";

import { configureApiClient, customFetch } from "./mutator";

/** 按序返回预设响应,并记录每次请求带的 Authorization。 */
function mockFetch(responses: Response[]): { auth: (string | null)[] } {
  const auth: (string | null)[] = [];
  let i = 0;
  vi.stubGlobal("fetch", (_url: string, init: RequestInit) => {
    auth.push(new Headers(init.headers).get("Authorization"));
    return Promise.resolve(responses[Math.min(i++, responses.length - 1)].clone());
  });
  return { auth };
}

const ok = () => new Response(JSON.stringify({ ok: true }), { status: 200 });
const unauthorized = () => new Response("", { status: 401 });

describe("customFetch 401 静默续期", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("续期成功后带新 token 重放一次", async () => {
    let token = "old";
    const refresh = vi.fn(() => {
      token = "new";
      return Promise.resolve(true);
    });
    const { auth } = mockFetch([unauthorized(), ok()]);
    configureApiClient({
      baseUrl: "",
      getToken: () => token,
      refreshToken: refresh,
      onUnauthorized: null,
    });

    await expect(customFetch("/api/v1/wallet", { method: "GET" })).resolves.toEqual({ ok: true });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(auth).toEqual(["Bearer old", "Bearer new"]);
  });

  it("并发 401 只续期一次(single-flight)", async () => {
    let token = "old";
    const refresh = vi.fn(async () => {
      await new Promise((r) => setTimeout(r, 5));
      token = "new";
      return true;
    });
    mockFetch([unauthorized(), unauthorized(), ok()]);
    configureApiClient({ baseUrl: "", getToken: () => token, refreshToken: refresh });

    await Promise.all([
      customFetch("/api/v1/wallet", { method: "GET" }),
      customFetch("/api/v1/instances", { method: "GET" }),
    ]);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("token 已被别的标签页换掉时不再续期,直接重放", async () => {
    // 模拟:请求发出用的是 old,进入临界区时 localStorage 里已是别的标签页续期后的 new。
    // 若此时仍调 refresh,就是拿已消费的 refresh token 重放 → 后端撤销该用户全部会话。
    const tokens = ["old", "new", "new"];
    let i = 0;
    const refresh = vi.fn(() => Promise.resolve(true));
    const { auth } = mockFetch([unauthorized(), ok()]);
    configureApiClient({
      baseUrl: "",
      getToken: () => tokens[Math.min(i++, tokens.length - 1)],
      refreshToken: refresh,
    });

    await customFetch("/api/v1/wallet", { method: "GET" });
    expect(refresh).not.toHaveBeenCalled();
    expect(auth).toEqual(["Bearer old", "Bearer new"]);
  });

  it("有 Web Locks 时经锁串行化(跨标签页互斥)", async () => {
    const request = vi.fn((_name: string, cb: () => Promise<boolean>) => cb());
    vi.stubGlobal("navigator", { locks: { request } });
    let token = "old";
    mockFetch([unauthorized(), ok()]);
    configureApiClient({
      baseUrl: "",
      getToken: () => token,
      refreshToken: () => {
        token = "new";
        return Promise.resolve(true);
      },
    });

    await customFetch("/api/v1/wallet", { method: "GET" });
    expect(request).toHaveBeenCalledWith("superdl:token-refresh", expect.any(Function));
  });
});
