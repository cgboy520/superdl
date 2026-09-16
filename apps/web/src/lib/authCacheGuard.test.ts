import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setupAuthCacheGuard } from "./authCacheGuard";
import { authStore } from "../stores/auth";

/** Fake JWT with the given sub (shape only, unsigned: the guard decodes the payload without verifying). */
function fakeJwt(sub: string): string {
  const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
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

  it("logout (token → null) clears the query cache", async () => {
    const qc = new QueryClient();
    const clear = vi.spyOn(qc, "clear");
    const un = setupAuthCacheGuard(qc);
    authStore.getState().login(fakeJwt("1"));
    authStore.getState().logout();
    await vi.waitFor(() => expect(clear).toHaveBeenCalledTimes(1));
    un();
  });

  it("silent renewal (same account, token A→A') keeps the cache: renewal happens hourly, clearing would blank the site every hour", async () => {
    const qc = new QueryClient();
    const clear = vi.spyOn(qc, "clear");
    const un = setupAuthCacheGuard(qc);
    authStore.getState().login(fakeJwt("1"));
    authStore.getState().login(fakeJwt("1"));
    await new Promise((r) => setTimeout(r, 20));
    expect(clear).not.toHaveBeenCalled();
    un();
  });

  it("switching accounts (sub A→B) clears the cache: otherwise B first sees A's balance and instances (cross-account leak)", async () => {
    const qc = new QueryClient();
    const clear = vi.spyOn(qc, "clear");
    const un = setupAuthCacheGuard(qc);
    authStore.getState().login(fakeJwt("1"));
    authStore.getState().login(fakeJwt("2"));
    await vi.waitFor(() => expect(clear).toHaveBeenCalledTimes(1));
    un();
  });

  it("an undecodable token with a different value counts as an account switch (clear when unsure, the safe direction)", async () => {
    const qc = new QueryClient();
    const clear = vi.spyOn(qc, "clear");
    const un = setupAuthCacheGuard(qc);
    authStore.getState().login("not-a-jwt-a");
    authStore.getState().login("not-a-jwt-b");
    await vi.waitFor(() => expect(clear).toHaveBeenCalledTimes(1));
    un();
  });
});
