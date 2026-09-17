import { beforeEach, describe, expect, it, vi } from "vitest";

import { configureApiClient, customFetch } from "./mutator";

/** Returns the preset responses in order and records the Authorization of every request. */
function mockFetch(responses: Response[]): { auth: (string | null)[] } {
  const auth: (string | null)[] = [];
  let i = 0;
  vi.stubGlobal("fetch", (_url: string, init: RequestInit) => {
    auth.push(new Headers(init.headers).get("Authorization"));
    const r = responses[Math.min(i++, responses.length - 1)];
    if (!r) return Promise.reject(new Error("no more responses"));
    return Promise.resolve(r.clone());
  });
  return { auth };
}

const ok = () => new Response(JSON.stringify({ ok: true }), { status: 200 });
const unauthorized = () => new Response("", { status: 401 });

/** LockManager stand-in that runs same-named requests serially. */
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

describe("customFetch 401 silent renewal", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.stubGlobal("navigator", { locks: fakeLocks() });
  });

  it("replays once with the new token after a successful renewal", async () => {
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

  it("concurrent 401s renew once: Web Locks mutual exclusion + stale-token re-check inside the critical section", async () => {
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

  it("does not renew when another tab already replaced the token, replays directly", async () => {
    const tokens = ["old", "new", "new"];
    let i = 0;
    const refresh = vi.fn(() => Promise.resolve(true));
    const { auth } = mockFetch([unauthorized(), ok()]);
    configureApiClient({
      baseUrl: "",
      getToken: () => tokens[Math.min(i++, tokens.length - 1)] ?? null,
      refreshToken: refresh,
    });

    await customFetch("/api/v1/wallet", { method: "GET" });
    expect(refresh).not.toHaveBeenCalled();
    expect(auth).toEqual(["Bearer old", "Bearer new"]);
  });
});

describe("Accept-Language", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.stubGlobal("navigator", { locks: fakeLocks() });
  });

  function mockFetchHeaders(responses: Response[]): { langs: (string | null)[] } {
    const langs: (string | null)[] = [];
    let i = 0;
    vi.stubGlobal("fetch", (_url: string, init: RequestInit) => {
      langs.push(new Headers(init.headers).get("Accept-Language"));
      const r = responses[Math.min(i++, responses.length - 1)];
      return Promise.resolve(r ? r.clone() : new Response("", { status: 500 }));
    });
    return { langs };
  }

  it("sends the configured locale on the initial request and on the 401 replay", async () => {
    let token = "old";
    const { langs } = mockFetchHeaders([unauthorized(), ok()]);
    configureApiClient({
      baseUrl: "",
      getToken: () => token,
      getLocale: () => "zh-CN",
      refreshToken: () => {
        token = "new";
        return Promise.resolve(true);
      },
      onUnauthorized: null,
    });
    await expect(customFetch("/api/v1/wallet", { method: "GET" })).resolves.toEqual({ ok: true });
    expect(langs).toEqual(["zh-CN", "zh-CN"]);
  });

  it("keeps an explicit per-request Accept-Language and sends none without a locale", async () => {
    const { langs } = mockFetchHeaders([ok(), ok()]);
    configureApiClient({ baseUrl: "", getToken: () => null, getLocale: () => "zh-CN", onUnauthorized: null });
    await customFetch("/api/v1/site-config", { method: "GET", headers: { "Accept-Language": "en-US" } });
    configureApiClient({ baseUrl: "", getToken: () => null, getLocale: () => null, onUnauthorized: null });
    await customFetch("/api/v1/site-config", { method: "GET" });
    expect(langs).toEqual(["en-US", null]);
  });
});

describe("error body parsing", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    configureApiClient({ baseUrl: "", getToken: () => null, refreshToken: null, onUnauthorized: null });
  });

  it("throws ApiError instead of SyntaxError when the gateway returns HTML with 502", async () => {
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

  it("passes code / message_key of a structured error body through", async () => {
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

  it("passes non-JSON success responses (text/csv export) through as text", async () => {
    mockFetch([
      new Response("a,b\r\n1,2\r\n", {
        status: 200,
        headers: { "Content-Type": "text/csv; charset=utf-8" },
      }),
    ]);
    await expect(customFetch("/api/v1/billing/export", { method: "GET" })).resolves.toBe("a,b\r\n1,2\r\n");
  });
});
