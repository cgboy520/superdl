/** Auth state: token and session generation persist atomically and sync across tabs. */

import { withAuthSessionLock } from "@superdl/api-client";
import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";

const TOKEN_KEY = "superdl.web.accessToken";

interface AuthSession {
  sessionId: string | null;
  accessToken: string | null;
}

interface AuthState extends AuthSession {
  login: (accessToken: string) => Promise<void>;
  /** Only for the refresh callback, which already holds withAuthSessionLock. */
  renewTokenUnderLock: (accessToken: string, expectedSessionId: string | null) => boolean;
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
  login: (accessToken) =>
    withAuthSessionLock(() => {
      const session = { sessionId: crypto.randomUUID(), accessToken };
      localStorage.setItem(TOKEN_KEY, JSON.stringify(session));
      set(session);
    }),
  renewTokenUnderLock: (accessToken, expectedSessionId) => {
    const current = readAuthSession();
    if (!expectedSessionId || current.sessionId !== expectedSessionId || !current.accessToken) return false;
    const session = { ...current, accessToken };
    localStorage.setItem(TOKEN_KEY, JSON.stringify(session));
    set(session);
    return true;
  },
  logout: (expectedSessionId = readAuthSession().sessionId) =>
    withAuthSessionLock(() => {
      if (readAuthSession().sessionId !== expectedSessionId) return false;
      const session = { sessionId: crypto.randomUUID(), accessToken: null };
      localStorage.setItem(TOKEN_KEY, JSON.stringify(session));
      set(session);
      return true;
    }),
}));

/** Read and synchronize before requests, even if the storage event has not arrived yet. */
export function readAuthSession(): AuthSession {
  const session = readStoredSession();
  const current = authStore.getState();
  if (current.sessionId !== session.sessionId || current.accessToken !== session.accessToken) {
    authStore.setState(session);
  }
  return session;
}

export function readAccessToken(): string | null {
  return readAuthSession().accessToken;
}

window.addEventListener("storage", (e) => {
  if (e.storageArea !== null && e.storageArea !== localStorage) return;
  if (e.key !== null && e.key !== TOKEN_KEY) return;
  readAuthSession();
});

export function useIsLoggedIn(): boolean {
  return useStore(authStore, (s) => s.accessToken !== null);
}
