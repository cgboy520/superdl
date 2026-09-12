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

/** 最小 LockManager:同名请求串行执行,模拟浏览器 Web Locks 的互斥语义(node/jsdom 均不实现)。 */
function fakeLocks(): Pick<LockManager, "request"> {
  const tails = new Map<string, Promise<unknown>>();
  return {
    request: ((name: string, cb: () => Promise<unknown>) => {
      const prev = tails.get(name) ?? Promise.resolve();
      const next = prev.then(cb, cb);
      tails.set(
        name,
        next.catch(() => undefined),
      );
      return next;
    }) as LockManager["request"],
  };
}

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

  it("并发 401 只续期一次:无 Web Locks 时靠同标签页内存 single-flight", async () => {
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

  it("并发 401 只续期一次:有 Web Locks 时靠临界区内的 stale-token 复检", async () => {
    vi.stubGlobal("navigator", { locks: fakeLocks() });
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
    // 第二个请求进入临界区时 token 已是 new ≠ 发起时的 old,直接重放而不再消费 refresh token
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("token 已被别的标签页换掉时不再续期,直接重放", async () => {
    // 请求以 old 发出,进入临界区时 token 已被别的标签页续成 new;此时再 refresh = 重放已消费 token,后端撤销全部会话。
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
});

describe("错误体解析", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    configureApiClient({ baseUrl: "", getToken: () => null, refreshToken: null, onUnauthorized: null });
  });

  it("网关 502 返回 HTML 时抛 ApiError,而不是 SyntaxError", async () => {
    mockFetch([
      new Response("<html><body>502 Bad Gateway</body></html>", {
        status: 502,
        headers: { "Content-Type": "text/html" },
      }),
    ]);
    await expect(customFetch("/api/v1/wallet", { method: "GET" })).rejects.toMatchObject({
      code: "HTTP_ERROR",
      status: 502,
    });
  });

  it("结构化错误体照常透出 code / message_key", async () => {
    mockFetch([
      new Response(JSON.stringify({ code: "INSUFFICIENT_BALANCE", message_key: "billing.x" }), {
        status: 400,
      }),
    ]);
    await expect(customFetch("/api/v1/instances", { method: "POST" })).rejects.toMatchObject({
      code: "INSUFFICIENT_BALANCE",
      message_key: "billing.x",
    });
  });

  it("非 JSON 成功响应(text/csv 导出)原样透传文本", async () => {
    // 注:Response.text() 按 UTF-8 解码会剥掉 BOM,下载落盘时由调用方补回
    mockFetch([
      new Response("a,b\r\n1,2\r\n", {
        status: 200,
        headers: { "Content-Type": "text/csv; charset=utf-8" },
      }),
    ]);
    await expect(customFetch("/api/v1/billing/export", { method: "GET" })).resolves.toBe("a,b\r\n1,2\r\n");
  });
});
