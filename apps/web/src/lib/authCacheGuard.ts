import type { QueryClient } from "@tanstack/react-query";

import { authStore } from "../stores/auth";

/** Account identity of the access token (JWT payload.sub, decoded without verification). */
function accountOf(token: string): string | null {
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const payload: unknown = JSON.parse(atob(part.replace(/-/g, "+").replace(/_/g, "/")));
    const sub = (payload as { sub?: unknown }).sub;
    return typeof sub === "string" ? sub : null;
  } catch {
    return null;
  }
}

/** Logout (including account switch and synced logout from another tab) clears the query cache: cancel in-flight queries, then clear; silent renewal (same sub) keeps it; an undecodable token with a different value counts as a switch. */
export function setupAuthCacheGuard(queryClient: QueryClient): () => void {
  return authStore.subscribe((state, prev) => {
    const loggedOut = prev.accessToken !== null && state.accessToken === null;
    let accountSwitched = false;
    if (prev.accessToken !== null && state.accessToken !== null) {
      const before = accountOf(prev.accessToken);
      const after = accountOf(state.accessToken);
      accountSwitched = before === null || after === null ? prev.accessToken !== state.accessToken : before !== after;
    }
    if (loggedOut || accountSwitched) {
      void queryClient.cancelQueries().then(() => queryClient.clear());
    }
  });
}
