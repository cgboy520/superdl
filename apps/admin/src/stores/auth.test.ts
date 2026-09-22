/** Session isolation and admin permissions. */
import { adminMeApiAdminV1MeGet, configureApiClient, withAuthSessionLock } from "@superdl/api-client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authStore, canReadInvoices, readAdminToken, readAuthSession } from "./auth";
import { queryClient } from "../lib/queryClient";

const TOKEN_KEY = "superdl.admin.accessToken";
const adminA = { id: 1, username: "admin-a", role: "admin" };
const adminB = { id: 2, username: "admin-b", role: "readonly" };

function otherTab(sessionId: string, accessToken: string | null): void {
  localStorage.setItem(TOKEN_KEY, JSON.stringify({ sessionId, accessToken }));
  window.dispatchEvent(new StorageEvent("storage", { key: TOKEN_KEY, storageArea: localStorage }));
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

describe("admin session boundaries", () => {
  beforeEach(async () => {
    vi.stubGlobal("navigator", { locks: fakeLocks() });
    localStorage.clear();
    readAuthSession();
    await authStore.getState().login("token-a", adminA);
    queryClient.setQueryData(["private"], "account-a");
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
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    localStorage.clear();
    readAuthSession();
    queryClient.clear();
  });

  it("local renewal preserves generation, identity and query cache", async () => {
    const sessionId = authStore.getState().sessionId;
    expect(await renew("renewed-a", sessionId)).toBe(true);
    expect(authStore.getState()).toMatchObject({ sessionId, accessToken: "renewed-a", admin: adminA });
    expect(readAdminToken()).toBe("renewed-a");
    expect(queryClient.getQueryData(["private"])).toBe("account-a");
  });

  it("same-session storage renewal preserves identity and query cache", () => {
    const sessionId = authStore.getState().sessionId;
    expect(sessionId).not.toBeNull();
    otherTab(sessionId ?? "", "renewed-a");
    expect(authStore.getState().admin).toEqual(adminA);
    expect(readAdminToken()).toBe("renewed-a");
    expect(queryClient.getQueryData(["private"])).toBe("account-a");
  });

  it.each([
    { sessionId: "account-b", accessToken: "token-b" },
    { sessionId: "account-a-new-login", accessToken: "token-a" },
    { sessionId: "logged-out", accessToken: null },
  ])("storage transition to $sessionId drops admin and private caches", ({ sessionId, accessToken }) => {
    const cancel = vi.spyOn(queryClient, "cancelQueries");
    otherTab(sessionId, accessToken);
    expect(authStore.getState()).toMatchObject({ sessionId, accessToken, admin: null });
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryData(["private"])).toBeUndefined();
  });

  it("clears mutation state as well as queries on a storage account switch", () => {
    queryClient.getMutationCache().build(queryClient, { mutationKey: ["old-write"] });
    otherTab("account-b", "token-b");
    expect(queryClient.getMutationCache().getAll()).toHaveLength(0);
  });

  it("storage clear drops the token, identity and private caches", () => {
    localStorage.clear();
    window.dispatchEvent(new StorageEvent("storage", { key: null, storageArea: localStorage }));
    expect(authStore.getState()).toMatchObject({ accessToken: null, admin: null });
    expect(queryClient.getQueryData(["private"])).toBeUndefined();
  });

  it("ignores unrelated and sessionStorage events", () => {
    const clear = vi.spyOn(queryClient, "clear");
    window.dispatchEvent(new StorageEvent("storage", { key: "unrelated", storageArea: localStorage }));
    window.dispatchEvent(new StorageEvent("storage", { key: TOKEN_KEY, storageArea: sessionStorage }));
    expect(clear).not.toHaveBeenCalled();
    expect(authStore.getState().admin).toEqual(adminA);
  });

  it("reads the latest record instead of resurrecting a delayed storage event payload", () => {
    localStorage.setItem(TOKEN_KEY, JSON.stringify({ sessionId: "account-b", accessToken: "token-b" }));
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: TOKEN_KEY,
        storageArea: localStorage,
        newValue: JSON.stringify({ sessionId: "account-a", accessToken: "token-a" }),
      }),
    );
    expect(readAdminToken()).toBe("token-b");
    expect(authStore.getState().admin).toBeNull();
  });

  it("rejects stale renewal and logout before the storage event arrives", async () => {
    const previous = authStore.getState().sessionId;
    localStorage.setItem(TOKEN_KEY, JSON.stringify({ sessionId: "account-b", accessToken: "token-b" }));
    expect(await renew("late-a", previous)).toBe(false);
    expect(await authStore.getState().logout(previous)).toBe(false);
    expect(readAdminToken()).toBe("token-b");
    expect(authStore.getState().admin).toBeNull();
    expect(queryClient.getQueryData(["private"])).toBeUndefined();
  });

  it("local account switch replaces identity and clears query caches", async () => {
    await authStore.getState().login("token-b", adminB);
    expect(authStore.getState().admin).toEqual(adminB);
    expect(queryClient.getQueryData(["private"])).toBeUndefined();
  });

  it("logout followed by same-account login never reuses the old generation", async () => {
    const previous = authStore.getState().sessionId;
    await authStore.getState().logout();
    expect(await renew("late-a", previous)).toBe(false);
    await authStore.getState().login("token-a", adminA);
    expect(authStore.getState().sessionId).not.toBe(previous);
    expect(await renew("late-a", previous)).toBe(false);
    expect(await authStore.getState().logout(previous)).toBe(false);
    expect(authStore.getState().admin).toEqual(adminA);
  });

  it("even explicit login with an identical token starts a fresh session", async () => {
    const previous = authStore.getState().sessionId;
    await authStore.getState().login("token-a", adminA);
    expect(authStore.getState().sessionId).not.toBe(previous);
    expect(queryClient.getQueryData(["private"])).toBeUndefined();
  });

  it.each([adminA, adminB])("login as $username waits for the refresh commit under the shared lock", async (admin) => {
    const previous = readAuthSession().sessionId;
    const release = deferredSignal();
    const entered = deferredSignal();
    const refresh = withAuthSessionLock(async () => {
      entered.resolve();
      await release.promise;
      return authStore.getState().renewTokenUnderLock("renewed-a", previous);
    });
    await entered.promise;
    const login = authStore.getState().login("new-login", admin);
    await Promise.resolve();
    expect(readAuthSession().sessionId).toBe(previous);
    expect(readAdminToken()).toBe("token-a");
    release.resolve();
    expect(await refresh).toBe(true);
    await login;
    expect(readAuthSession().sessionId).not.toBe(previous);
    expect(authStore.getState().admin).toEqual(admin);
    expect(await renew("late-a", previous)).toBe(false);
    expect(readAdminToken()).toBe("new-login");
  });

  it("explicit logout waits for renewal without deadlocking and clears identity", async () => {
    const previous = readAuthSession().sessionId;
    const release = deferredSignal();
    const entered = deferredSignal();
    const refresh = withAuthSessionLock(async () => {
      entered.resolve();
      await release.promise;
      return authStore.getState().renewTokenUnderLock("renewed-a", previous);
    });
    await entered.promise;
    const logout = authStore.getState().logout();
    await Promise.resolve();
    expect(readAuthSession().sessionId).toBe(previous);
    release.resolve();
    expect(await refresh).toBe(true);
    expect(await logout).toBe(true);
    expect(readAdminToken()).toBeNull();
    expect(authStore.getState().admin).toBeNull();
    expect(queryClient.getQueryData(["private"])).toBeUndefined();
  });

  it.each([true, false])(
    "queued logout checks its initiating session inside the lock (explicit expected: %s)",
    async (explicit) => {
      const previous = readAuthSession().sessionId;
      const release = deferredSignal();
      const holder = withAuthSessionLock(() => release.promise);
      const login = authStore.getState().login("token-b", adminB);
      const logout = explicit ? authStore.getState().logout(previous) : authStore.getState().logout();
      release.resolve();
      await holder;
      await login;
      expect(await logout).toBe(false);
      expect(readAdminToken()).toBe("token-b");
      expect(authStore.getState().admin).toEqual(adminB);
    },
  );

  it("late me identity and a late 401 cannot affect B", async () => {
    const previous = readAuthSession().sessionId;
    const response = deferred<Response>();
    vi.stubGlobal("fetch", () => response.promise);
    const result = adminMeApiAdminV1MeGet();
    const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
    await authStore.getState().login("token-b", adminB);
    const current = readAuthSession();
    response.resolve(new Response("", { status: 401 }));
    await rejected;
    expect(authStore.getState().setAdmin(adminA, previous)).toBe(false);
    expect(authStore.getState().admin).toEqual(adminB);
    expect(readAuthSession()).toEqual(current);
    expect(authStore.getState().setAdmin({ ...adminB, role: "ops" }, current.sessionId)).toBe(true);
    expect(authStore.getState().admin?.role).toBe("ops");
  });

  it.each([true, false])("real renewal releases the lock before unauthorized logout (%s)", async (refreshed) => {
    const refresh = vi.fn((sessionId: string | null) =>
      Promise.resolve(refreshed ? authStore.getState().renewTokenUnderLock("renewed-a", sessionId) : false),
    );
    configureApiClient({ refreshToken: refresh });
    const fetchMock = vi.fn(() => Promise.resolve(new Response("", { status: 401 })));
    vi.stubGlobal("fetch", fetchMock);
    await expect(adminMeApiAdminV1MeGet()).rejects.toMatchObject({ status: 401 });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(refreshed ? 2 : 1);
    expect(readAdminToken()).toBeNull();
    expect(authStore.getState().admin).toBeNull();
  });

  it("storage synchronization never writes back or generates sessions", () => {
    localStorage.setItem(TOKEN_KEY, JSON.stringify({ sessionId: "session-b", accessToken: "token-b" }));
    const write = vi.spyOn(Storage.prototype, "setItem");
    const generation = vi.spyOn(crypto, "randomUUID");
    const clear = vi.spyOn(queryClient, "clear");
    for (let i = 0; i < 3; i++) {
      window.dispatchEvent(new StorageEvent("storage", { key: TOKEN_KEY, storageArea: localStorage }));
      readAuthSession();
    }
    expect(write).not.toHaveBeenCalled();
    expect(generation).not.toHaveBeenCalled();
    expect(clear).toHaveBeenCalledTimes(1);
    expect(readAuthSession().sessionId).toBe("session-b");
  });

  it("legacy token-only records cannot establish session continuity", () => {
    localStorage.setItem(TOKEN_KEY, "legacy-token");
    expect(readAdminToken()).toBeNull();
    expect(authStore.getState().admin).toBeNull();
    expect(queryClient.getQueryData(["private"])).toBeUndefined();
  });

  it("old cancellation settling cannot erase the new session's data", async () => {
    let finish!: () => void;
    vi.spyOn(queryClient, "cancelQueries").mockReturnValue(
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
    );
    await authStore.getState().login("token-b", adminB);
    queryClient.setQueryData(["private"], "account-b");
    finish();
    await Promise.resolve();
    expect(queryClient.getQueryData(["private"])).toBe("account-b");
  });

  it("an old query completing after a storage switch cannot repopulate the new cache", async () => {
    let finish!: (value: string) => void;
    const result = queryClient.query({
      queryKey: ["late"],
      queryFn: () =>
        new Promise<string>((resolve) => {
          finish = resolve;
        }),
    });
    const rejected = expect(result).rejects.toBeDefined();
    otherTab("account-b", "token-b");
    queryClient.setQueryData(["late"], "account-b");
    finish("account-a");
    await rejected;
    expect(queryClient.getQueryData(["late"])).toBe("account-b");
  });
});

describe("canReadInvoices", () => {
  it("only finance and admin can read invoices (ops/readonly never)", () => {
    expect(canReadInvoices("finance")).toBe(true);
    expect(canReadInvoices("admin")).toBe(true);
    expect(canReadInvoices("ops")).toBe(false);
    expect(canReadInvoices("readonly")).toBe(false);
  });
});
