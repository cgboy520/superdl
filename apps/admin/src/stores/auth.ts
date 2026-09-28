/** Admin auth state: token + admin identity; storage keys isolated from the user console. */

import { withAuthSessionLock } from "@superdl/api-client";
import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";

import { queryClient } from "../lib/queryClient";

const TOKEN_KEY = "superdl.admin.accessToken";

export interface AdminInfo {
  id: number;
  username: string;
  role: string;
}

interface AuthSession {
  sessionId: string | null;
  accessToken: string | null;
}

interface AuthState extends AuthSession {
  admin: AdminInfo | null;
  login: (accessToken: string, admin: AdminInfo) => Promise<void>;
  /** Only for the refresh callback, which already holds withAuthSessionLock. */
  renewTokenUnderLock: (accessToken: string, expectedSessionId: string | null) => boolean;
  /** Calibrate identity only for the session that requested /me. */
  setAdmin: (admin: AdminInfo, expectedSessionId: string | null) => boolean;
  logout: (expectedSessionId?: string | null) => Promise<boolean>;
}

function readStoredSession(): AuthSession {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(TOKEN_KEY) ?? "null");
    if (
      typeof value === "object" &&
      value !== null &&
      "sessionId" in value &&
      typeof value.sessionId === "string" &&
      value.sessionId !== "" &&
      "accessToken" in value &&
      (typeof value.accessToken === "string" || value.accessToken === null)
    ) {
      return { sessionId: value.sessionId, accessToken: value.accessToken };
    }
  } catch {
    // Legacy token-only records cannot establish session continuity: require login.
  }
  return { sessionId: null, accessToken: null };
}

export const authStore = createStore<AuthState>()((set) => ({
  ...readStoredSession(),
  admin: null,
  login: (accessToken, admin) =>
    withAuthSessionLock(() => {
      const session = { sessionId: crypto.randomUUID(), accessToken };
      localStorage.setItem(TOKEN_KEY, JSON.stringify(session));
      set({ ...session, admin });
    }),
  renewTokenUnderLock: (accessToken, expectedSessionId) => {
    const current = readAuthSession();
    if (!expectedSessionId || current.sessionId !== expectedSessionId || !current.accessToken) return false;
    const session = { ...current, accessToken };
    localStorage.setItem(TOKEN_KEY, JSON.stringify(session));
    set(session);
    return true;
  },
  setAdmin: (admin, expectedSessionId) => {
    const current = readAuthSession();
    if (!current.accessToken || current.sessionId !== expectedSessionId) return false;
    set({ admin });
    return true;
  },
  logout: (expectedSessionId = readAuthSession().sessionId) =>
    withAuthSessionLock(() => {
      if (readAuthSession().sessionId !== expectedSessionId) return false;
      const session = { sessionId: crypto.randomUUID(), accessToken: null };
      localStorage.setItem(TOKEN_KEY, JSON.stringify(session));
      set({ ...session, admin: null });
      return true;
    }),
}));

// Clear synchronously: a deferred clear could erase data already fetched for the new session.
authStore.subscribe((state, prev) => {
  if (state.sessionId !== prev.sessionId) {
    void queryClient.cancelQueries();
    queryClient.clear();
  }
});

/** Read and synchronize before requests, even if the storage event has not arrived yet. */
export function readAuthSession(): AuthSession {
  const session = readStoredSession();
  const current = authStore.getState();
  if (current.sessionId !== session.sessionId || current.accessToken !== session.accessToken) {
    authStore.setState({
      ...session,
      admin: current.sessionId === session.sessionId ? current.admin : null,
    });
  }
  return session;
}

window.addEventListener("storage", (e) => {
  if (e.storageArea !== null && e.storageArea !== localStorage) return;
  if (e.key !== null && e.key !== TOKEN_KEY) return;
  readAuthSession();
});

export function useAuth(): AuthState {
  return useStore(authStore);
}

/** Request path reads localStorage (cross-tab renewal takes effect at once). */
export function readAdminToken(): string | null {
  return readAuthSession().accessToken;
}

export function useAdminRole(): string {
  return useStore(authStore, (s) => s.admin?.role ?? "readonly");
}

/** Resource write permission (SKU/instances/tenants): admin·ops */
export function canWriteOps(role: string): boolean {
  return role === "admin" || role === "ops";
}

/** Finance write permission (create/review adjustments): admin·finance */
export function canWriteFinance(role: string): boolean {
  return role === "admin" || role === "finance";
}

/** Invoice read permission (backend require_roles("finance")): admin·finance; a separate rule from canWriteFinance. */
export function canReadInvoices(role: string): boolean {
  return role === "admin" || role === "finance";
}
