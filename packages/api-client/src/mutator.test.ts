import { beforeEach, describe, expect, it, vi } from "vitest";

import { configureApiClient as configureClient, customFetch, withAuthSessionLock } from "./mutator";

/** Existing transport cases all run within one session, regardless of token rotation. */
function configureApiClient(opts: Parameters<typeof configureClient>[0]): void {
  configureClient({
    ...opts,
    getSession: () => ({ sessionId: "session-a", accessToken: opts.getToken?.() ?? null }),
  });
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function deferredSignal(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

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
    let token = "old";
    const refresh = vi.fn(() => Promise.resolve(true));
    const auth: (string | null)[] = [];
    vi.stubGlobal("fetch", (_url: string, init: RequestInit) => {
      auth.push(new Headers(init.headers).get("Authorization"));
      token = "new";
      return Promise.resolve(auth.length === 1 ? unauthorized() : ok());
    });
    configureApiClient({
      baseUrl: "",
      getToken: () => token,
      refreshToken: refresh,
    });

    await customFetch("/api/v1/wallet", { method: "GET" });
    expect(refresh).not.toHaveBeenCalled();
    expect(auth).toEqual(["Bearer old", "Bearer new"]);
  });
});

describe("request session boundaries", () => {
  let session: { sessionId: string | null; accessToken: string | null };
  const refresh = vi.fn<() => Promise<boolean>>();
  const logout = vi.fn();

  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.stubGlobal("navigator", { locks: fakeLocks() });
    session = { sessionId: "session-a", accessToken: "token-a" };
    refresh.mockReset().mockResolvedValue(false);
    logout.mockReset();
    configureClient({
      baseUrl: "",
      getToken: () => null,
      getSession: () => ({ ...session }),
      getLocale: () => null,
      refreshToken: refresh,
      onUnauthorized: logout,
    });
  });

  it.each([
    { name: "another account", sessionId: "session-b", accessToken: "token-b" },
    { name: "logout", sessionId: "logged-out", accessToken: null },
    { name: "same-account re-login with an identical token", sessionId: "session-a2", accessToken: "token-a" },
  ])("never replays a POST or logs out the new session after $name", async ({ sessionId, accessToken }) => {
    const pending = deferred<Response>();
    const fetchMock = vi.fn(() => pending.promise);
    vi.stubGlobal("fetch", fetchMock);
    const result = customFetch("/api/v1/instances", { method: "POST", body: "{}" });
    const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
    session = { sessionId, accessToken };
    pending.resolve(unauthorized());
    await rejected;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(refresh).not.toHaveBeenCalled();
    expect(logout).not.toHaveBeenCalled();
  });

  it("replays a same-session POST once with its original body and idempotency key", async () => {
    const attempts: RequestInit[] = [];
    vi.stubGlobal("fetch", (_url: string, init: RequestInit) => {
      attempts.push(init);
      return Promise.resolve(attempts.length === 1 ? unauthorized() : ok());
    });
    refresh.mockImplementation(() => {
      session = { ...session, accessToken: "renewed-a" };
      return Promise.resolve(true);
    });
    await expect(
      customFetch("/api/v1/instances", {
        method: "POST",
        body: JSON.stringify({ name: "test-instance" }),
        headers: { "Idempotency-Key": "same-operation" },
      }),
    ).resolves.toEqual({ ok: true });
    expect(attempts).toHaveLength(2);
    expect(attempts.map((init) => init.body)).toEqual(Array<string>(2).fill('{"name":"test-instance"}'));
    expect(attempts.map((init) => new Headers(init.headers).get("Idempotency-Key"))).toEqual([
      "same-operation",
      "same-operation",
    ]);
    expect(attempts.map((init) => new Headers(init.headers).get("Authorization"))).toEqual([
      "Bearer token-a",
      "Bearer renewed-a",
    ]);
    expect(refresh).toHaveBeenCalledExactlyOnceWith("session-a");
    expect(logout).not.toHaveBeenCalled();
  });

  it("checks the session again after waiting for the cross-tab refresh lock", async () => {
    const release = deferredSignal();
    const locks = fakeLocks();
    const requested = vi.spyOn(locks, "request");
    vi.stubGlobal("navigator", { locks });
    const holder = locks.request("superdl:token-refresh", () => release.promise);
    const fetchMock = vi.fn(() => Promise.resolve(unauthorized()));
    vi.stubGlobal("fetch", fetchMock);
    const result = customFetch("/api/v1/instances", { method: "POST" });
    const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
    await vi.waitFor(() => expect(requested).toHaveBeenCalledTimes(2));
    session = { sessionId: "session-b", accessToken: "token-b" };
    release.resolve();
    await holder;
    await rejected;
    expect(refresh).not.toHaveBeenCalled();
    expect(logout).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([true, false])("rejects a stale refresh result (%s) without replay or logout", async (success) => {
    const pending = deferred<boolean>();
    refresh.mockReturnValue(pending.promise);
    const fetchMock = vi.fn(() => Promise.resolve(unauthorized()));
    vi.stubGlobal("fetch", fetchMock);
    const result = customFetch("/api/v1/instances", { method: "POST" });
    const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    session = { sessionId: "session-b", accessToken: "token-b" };
    pending.resolve(success);
    await rejected;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(logout).not.toHaveBeenCalled();
  });

  it.each([200, 401, 500])(
    "rejects a late %s response instead of delivering it to the next session",
    async (status) => {
      const pending = deferred<Response>();
      vi.stubGlobal("fetch", () => pending.promise);
      const result = customFetch("/api/v1/me", { method: "GET" });
      const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
      session = { sessionId: "session-b", accessToken: "token-b" };
      pending.resolve(new Response(JSON.stringify({ account: "a" }), { status }));
      await rejected;
      expect(logout).not.toHaveBeenCalled();
    },
  );

  it.each([200, 401])("rechecks the session after reading the %s response body", async (status) => {
    const pending = deferred<string>();
    const response = new Response("", { status });
    const text = vi.spyOn(response, "text").mockReturnValue(pending.promise);
    vi.stubGlobal("fetch", () => Promise.resolve(response));
    configureClient({ refreshToken: null });
    const result = customFetch("/api/v1/me", { method: "GET" });
    const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
    await vi.waitFor(() => expect(text).toHaveBeenCalledTimes(1));
    session = { sessionId: "session-b", accessToken: "token-b" };
    pending.resolve(JSON.stringify({ account: "a" }));
    await rejected;
    expect(logout).not.toHaveBeenCalled();
  });

  it("rejects a late replay response when the session changes after renewal", async () => {
    const pending = deferred<Response>();
    const fetchMock = vi.fn().mockResolvedValueOnce(unauthorized()).mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetchMock);
    refresh.mockImplementation(() => {
      session = { ...session, accessToken: "renewed-a" };
      return Promise.resolve(true);
    });
    const result = customFetch("/api/v1/me", { method: "GET" });
    const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    session = { sessionId: "session-b", accessToken: "token-b" };
    pending.resolve(ok());
    await rejected;
    expect(logout).not.toHaveBeenCalled();
  });

  it("does not replay an aborted POST even when renewal succeeds", async () => {
    const pending = deferred<boolean>();
    refresh.mockReturnValue(pending.promise);
    const fetchMock = vi.fn(() => Promise.resolve(unauthorized()));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const result = customFetch("/api/v1/instances", { method: "POST", signal: controller.signal });
    const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    controller.abort();
    pending.resolve(true);
    await rejected;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(logout).not.toHaveBeenCalled();
  });

  it("logs out only the original session when its own renewal fails", async () => {
    mockFetch([unauthorized()]);
    await expect(customFetch("/api/v1/me", { method: "GET" })).rejects.toMatchObject({ status: 401 });
    expect(logout).toHaveBeenCalledExactlyOnceWith("session-a");
  });

  it("logs out once after a same-session retry is still unauthorized", async () => {
    const { auth } = mockFetch([unauthorized(), unauthorized()]);
    refresh.mockImplementation(() => {
      session = { ...session, accessToken: "renewed-a" };
      return Promise.resolve(true);
    });
    await expect(customFetch("/api/v1/me", { method: "GET" })).rejects.toMatchObject({ status: 401 });
    expect(auth).toHaveLength(2);
    expect(logout).toHaveBeenCalledExactlyOnceWith("session-a");
  });

  it("awaits async unauthorized handling before rejecting the request", async () => {
    const release = deferredSignal();
    logout.mockImplementation(() => release.promise);
    mockFetch([unauthorized()]);
    const settled = vi.fn();
    const result = customFetch("/api/v1/me", { method: "GET" });
    const rejected = expect(result).rejects.toMatchObject({ status: 401 });
    void result.then(settled, settled);
    await vi.waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
    expect(settled).not.toHaveBeenCalled();
    release.resolve();
    await rejected;
    expect(settled).toHaveBeenCalledTimes(1);
  });

  it("releases the refresh lock before unauthorized handling acquires the same lock", async () => {
    mockFetch([unauthorized()]);
    logout.mockImplementation(async (expectedSessionId: string | null) => {
      await withAuthSessionLock(() => {
        expect(session.sessionId).toBe(expectedSessionId);
        session = { sessionId: "logged-out", accessToken: null };
      });
    });
    await expect(customFetch("/api/v1/me", { method: "GET" })).rejects.toMatchObject({ status: 401 });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(logout).toHaveBeenCalledTimes(1);
    expect(session.accessToken).toBeNull();
  });

  it("the exported persistence lock serializes explicit login behind an in-flight refresh", async () => {
    const release = deferredSignal();
    refresh.mockImplementation(async () => {
      await release.promise;
      session = { ...session, accessToken: "renewed-a" };
      return true;
    });
    const fetchMock = vi.fn(() => Promise.resolve(unauthorized()));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const result = customFetch("/api/v1/instances", { method: "POST", signal: controller.signal });
    const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    const login = withAuthSessionLock(() => {
      session = { sessionId: "session-b", accessToken: "token-b" };
    });
    await Promise.resolve();
    expect(session.sessionId).toBe("session-a");
    controller.abort();
    release.resolve();
    await login;
    await rejected;
    expect(session).toEqual({ sessionId: "session-b", accessToken: "token-b" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(logout).not.toHaveBeenCalled();
  });

  it("does not renew or log out for an explicit Authorization header", async () => {
    mockFetch([unauthorized()]);
    await expect(
      customFetch("/api/v1/me", { method: "GET", headers: { Authorization: "Basic test-only" } }),
    ).rejects.toMatchObject({ status: 401 });
    expect(refresh).not.toHaveBeenCalled();
    expect(logout).not.toHaveBeenCalled();
  });

  it("fails closed on token changes when session callbacks are not configured", async () => {
    const pending = deferred<Response>();
    vi.stubGlobal("fetch", () => pending.promise);
    configureClient({ getSession: null, getToken: () => session.accessToken });
    const result = customFetch("/api/v1/instances", { method: "POST" });
    const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
    session = { sessionId: "session-b", accessToken: "token-b" };
    pending.resolve(unauthorized());
    await rejected;
    expect(refresh).not.toHaveBeenCalled();
    expect(logout).not.toHaveBeenCalled();
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
