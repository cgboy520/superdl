import { configureApiClient, meApiV1MeGet, withAuthSessionLock } from "@superdl/api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setupAuthCacheGuard } from "./authCacheGuard";
import { useApiMutation, useLogout } from "../api/mutations";
import { authStore, readAccessToken, readAuthSession } from "../stores/auth";

const TOKEN_KEY = "superdl.web.accessToken";

function otherTab(sessionId: string, accessToken: string | null): void {
  localStorage.setItem(TOKEN_KEY, JSON.stringify({ sessionId, accessToken }));
  window.dispatchEvent(new StorageEvent("storage", { key: TOKEN_KEY, storageArea: localStorage }));
}

/** Unsigned fixture: identical subjects still need distinct generations after explicit login. */
function fakeJwt(sub: string, nonce = "initial"): string {
  const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub, nonce })}.sig`;
}

function deferred<T>() {
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

/** Same-name locks serialize separate tab writers as well as this tab's refresh. */
function fakeLocks(): Pick<LockManager, "request"> {
  const tails = new Map<string, Promise<unknown>>();
  return {
    request: ((name: string, action: () => unknown) => {
      const next = (tails.get(name) ?? Promise.resolve()).then(action);
      tails.set(
        name,
        next.catch(() => undefined),
      );
      return next;
    }) as LockManager["request"],
  };
}

function renew(token: string, sessionId: string | null) {
  return withAuthSessionLock(() => authStore.getState().renewTokenUnderLock(token, sessionId));
}

describe("setupAuthCacheGuard", () => {
  let qc: QueryClient;
  let unsubscribe: () => void;

  beforeEach(async () => {
    vi.stubGlobal("navigator", { locks: fakeLocks() });
    localStorage.clear();
    readAuthSession();
    await authStore.getState().login(fakeJwt("1"));
    qc = new QueryClient();
    qc.setQueryData(["private"], "account-a");
    unsubscribe = setupAuthCacheGuard(qc);
    configureApiClient({
      baseUrl: "",
      getSession: readAuthSession,
      getLocale: () => null,
      refreshToken: null,
      onUnauthorized: async (sessionId) => {
        await authStore.getState().logout(sessionId);
      },
    });
  });

  afterEach(() => {
    unsubscribe();
    qc.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    localStorage.clear();
    readAuthSession();
  });

  it("logout clears queries synchronously at the locked commit", async () => {
    const cancel = vi.spyOn(qc, "cancelQueries");
    await authStore.getState().logout();
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(qc.getQueryData(["private"])).toBeUndefined();
    expect(readAccessToken()).toBeNull();
  });

  it("silent renewal changes the token but preserves generation and cached data", async () => {
    const sessionId = authStore.getState().sessionId;
    expect(await renew(fakeJwt("1", "renewed"), sessionId)).toBe(true);
    expect(authStore.getState().sessionId).toBe(sessionId);
    expect(readAccessToken()).toBe(fakeJwt("1", "renewed"));
    expect(qc.getQueryData(["private"])).toBe("account-a");
  });

  it.each(["1", "2"])("explicit login to subject %s always creates a new session and clears data", async (sub) => {
    const previous = authStore.getState().sessionId;
    await authStore.getState().login(fakeJwt(sub));
    expect(authStore.getState().sessionId).not.toBe(previous);
    expect(qc.getQueryData(["private"])).toBeUndefined();
  });

  it("logout then login to the same account does not revive the old generation", async () => {
    const previous = authStore.getState().sessionId;
    await authStore.getState().logout();
    await authStore.getState().login(fakeJwt("1"));
    expect(authStore.getState().sessionId).not.toBe(previous);
    expect(await renew(fakeJwt("1", "late"), previous)).toBe(false);
    expect(await authStore.getState().logout(previous)).toBe(false);
    expect(readAccessToken()).toBe(fakeJwt("1"));
  });

  it("a stale refresh cannot restore a logged-out session", async () => {
    const previous = authStore.getState().sessionId;
    await authStore.getState().logout();
    expect(await renew(fakeJwt("1", "late"), previous)).toBe(false);
    expect(readAccessToken()).toBeNull();
  });

  it.each([
    { sessionId: "other-login", accessToken: fakeJwt("2") },
    { sessionId: "same-account-new-login", accessToken: fakeJwt("1") },
    { sessionId: "other-logout", accessToken: null },
  ])("storage transition to $sessionId clears data", ({ sessionId, accessToken }) => {
    otherTab(sessionId, accessToken);
    expect(qc.getQueryData(["private"])).toBeUndefined();
    expect(authStore.getState()).toMatchObject({ sessionId, accessToken });
  });

  it("storage renewal within the same generation keeps data", () => {
    const sessionId = authStore.getState().sessionId;
    expect(sessionId).not.toBeNull();
    otherTab(sessionId ?? "", fakeJwt("1", "renewed"));
    expect(qc.getQueryData(["private"])).toBe("account-a");
  });

  it("request reads synchronize a tab switch before the storage event arrives", async () => {
    const previous = authStore.getState().sessionId;
    localStorage.setItem(TOKEN_KEY, JSON.stringify({ sessionId: "session-b", accessToken: fakeJwt("2") }));
    expect(await renew(fakeJwt("1", "late"), previous)).toBe(false);
    expect(await authStore.getState().logout(previous)).toBe(false);
    expect(readAccessToken()).toBe(fakeJwt("2"));
    expect(qc.getQueryData(["private"])).toBeUndefined();
  });

  it.each(["1", "2"])("login to subject %s waits for a refresh commit under the shared lock", async (sub) => {
    const previous = readAuthSession().sessionId;
    const release = deferredSignal();
    const entered = deferredSignal();
    const refresh = withAuthSessionLock(async () => {
      entered.resolve();
      await release.promise;
      return authStore.getState().renewTokenUnderLock(fakeJwt("1", "renewed"), previous);
    });
    await entered.promise;
    const login = authStore.getState().login(fakeJwt(sub));
    await Promise.resolve();
    expect(readAuthSession().sessionId).toBe(previous);
    expect(readAccessToken()).toBe(fakeJwt("1"));
    release.resolve();
    expect(await refresh).toBe(true);
    await login;
    expect(readAuthSession().sessionId).not.toBe(previous);
    expect(readAccessToken()).toBe(fakeJwt(sub));
    expect(await renew(fakeJwt("1", "late"), previous)).toBe(false);
    expect(readAccessToken()).toBe(fakeJwt(sub));
  });

  it("explicit logout waits for renewal and completes without reacquiring its held lock", async () => {
    const previous = readAuthSession().sessionId;
    const release = deferredSignal();
    const entered = deferredSignal();
    const refresh = withAuthSessionLock(async () => {
      entered.resolve();
      await release.promise;
      return authStore.getState().renewTokenUnderLock(fakeJwt("1", "renewed"), previous);
    });
    await entered.promise;
    const logout = authStore.getState().logout();
    await Promise.resolve();
    expect(readAuthSession().sessionId).toBe(previous);
    release.resolve();
    expect(await refresh).toBe(true);
    expect(await logout).toBe(true);
    expect(readAccessToken()).toBeNull();
    expect(qc.getQueryData(["private"])).toBeUndefined();
  });

  it.each([true, false])(
    "queued logout checks its initiating session inside the lock (explicit expected: %s)",
    async (explicit) => {
      const previous = readAuthSession().sessionId;
      const release = deferredSignal();
      const holder = withAuthSessionLock(() => release.promise);
      const login = authStore.getState().login(fakeJwt("2"));
      const logout = explicit ? authStore.getState().logout(previous) : authStore.getState().logout();
      release.resolve();
      await holder;
      await login;
      expect(await logout).toBe(false);
      expect(readAccessToken()).toBe(fakeJwt("2"));
    },
  );

  it("a late 401 cannot log out B through the real mutator and store", async () => {
    const response = deferred<Response>();
    const fetchMock = vi.fn(() => response.promise);
    vi.stubGlobal("fetch", fetchMock);
    const result = meApiV1MeGet();
    const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
    await authStore.getState().login(fakeJwt("2"));
    const current = readAuthSession();
    response.resolve(new Response("", { status: 401 }));
    await rejected;
    expect(readAuthSession()).toEqual(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([true, false])(
    "real renewal followed by an unauthorized response releases the lock before logout (%s)",
    async (refreshed) => {
      const refresh = vi.fn((sessionId: string | null) =>
        Promise.resolve(
          refreshed ? authStore.getState().renewTokenUnderLock(fakeJwt("1", "renewed"), sessionId) : false,
        ),
      );
      configureApiClient({ refreshToken: refresh });
      const fetchMock = vi.fn(() => Promise.resolve(new Response("", { status: 401 })));
      vi.stubGlobal("fetch", fetchMock);
      await expect(meApiV1MeGet()).rejects.toMatchObject({ status: 401 });
      expect(refresh).toHaveBeenCalledTimes(1);
      expect(fetchMock).toHaveBeenCalledTimes(refreshed ? 2 : 1);
      expect(readAccessToken()).toBeNull();
    },
  );

  it("storage synchronization never writes back or generates sessions", () => {
    localStorage.setItem(TOKEN_KEY, JSON.stringify({ sessionId: "session-b", accessToken: fakeJwt("2") }));
    const write = vi.spyOn(Storage.prototype, "setItem");
    const generation = vi.spyOn(crypto, "randomUUID");
    const clear = vi.spyOn(qc, "clear");
    for (let i = 0; i < 3; i++) {
      window.dispatchEvent(new StorageEvent("storage", { key: TOKEN_KEY, storageArea: localStorage }));
      readAuthSession();
    }
    expect(write).not.toHaveBeenCalled();
    expect(generation).not.toHaveBeenCalled();
    expect(clear).toHaveBeenCalledTimes(1);
    expect(readAuthSession().sessionId).toBe("session-b");
  });

  it("login mutation stays pending until its asynchronous success callback commits the session", async () => {
    const release = deferredSignal();
    const entered = deferredSignal();
    const holder = withAuthSessionLock(() => release.promise);
    const finished = vi.fn();
    const { result } = renderHook(
      () =>
        useApiMutation(() => Promise.resolve("ok"), {
          invalidates: [],
          silentError: true,
          onSuccess: async () => {
            entered.resolve();
            await authStore.getState().login(fakeJwt("2"));
            finished();
          },
        }),
      { wrapper: ({ children }) => createElement(QueryClientProvider, { client: qc }, children) },
    );
    let completion!: Promise<string>;
    act(() => {
      completion = result.current.mutateAsync();
    });
    await entered.promise;
    await waitFor(() => expect(result.current.isPending).toBe(true));
    expect(finished).not.toHaveBeenCalled();
    expect(readAccessToken()).toBe(fakeJwt("1"));
    release.resolve();
    await act(async () => {
      await holder;
      await completion;
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(finished).toHaveBeenCalledTimes(1);
    expect(readAccessToken()).toBe(fakeJwt("2"));
  });

  it("an asynchronous success callback failure rejects mutateAsync instead of escaping the mutation", async () => {
    const error = new Error("Persistence failed");
    const { result } = renderHook(
      () =>
        useApiMutation(() => Promise.resolve("ok"), {
          invalidates: [],
          silentError: true,
          onSuccess: () => Promise.reject(error),
        }),
      { wrapper: ({ children }) => createElement(QueryClientProvider, { client: qc }, children) },
    );
    await act(async () => {
      await expect(result.current.mutateAsync()).rejects.toBe(error);
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it.each(["current", "all"] as const)("delayed explicit %s logout does not clear a newer login", async (scope) => {
    const response = deferred<Response>();
    vi.stubGlobal("fetch", () => response.promise);
    const { result } = renderHook(() => useLogout());
    const pending = result.current(scope);
    await authStore.getState().login(fakeJwt("2"));
    const current = readAuthSession();
    response.resolve(new Response(null, { status: 204 }));
    await pending;
    expect(readAuthSession()).toEqual(current);
  });

  it("storage clear and legacy token-only records fail closed", () => {
    localStorage.clear();
    window.dispatchEvent(new StorageEvent("storage", { key: null, storageArea: localStorage }));
    expect(readAccessToken()).toBeNull();
    expect(qc.getQueryData(["private"])).toBeUndefined();
    localStorage.setItem(TOKEN_KEY, fakeJwt("1"));
    expect(readAccessToken()).toBeNull();
  });

  it("does not clear the next session's cache when old cancellation settles", async () => {
    let finish!: () => void;
    vi.spyOn(qc, "cancelQueries").mockReturnValue(
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
    );
    await authStore.getState().login(fakeJwt("2"));
    qc.setQueryData(["private"], "account-b");
    finish();
    await Promise.resolve();
    expect(qc.getQueryData(["private"])).toBe("account-b");
  });

  it("cancels an old query so its late result cannot repopulate the cache", async () => {
    let finish!: (value: string) => void;
    const result = qc.query({
      queryKey: ["late"],
      queryFn: () =>
        new Promise<string>((resolve) => {
          finish = resolve;
        }),
    });
    const rejected = expect(result).rejects.toBeDefined();
    await authStore.getState().login(fakeJwt("2"));
    qc.setQueryData(["late"], "account-b");
    finish("account-a");
    await rejected;
    expect(qc.getQueryData(["late"])).toBe("account-b");
  });
});
