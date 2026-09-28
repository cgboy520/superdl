import type { QueryClient } from "@tanstack/react-query";

import { authStore } from "../stores/auth";

/** Clear at the locked login/logout commit, before its promise resolves; renewal alone keeps caches. */
export function setupAuthCacheGuard(queryClient: QueryClient): () => void {
  return authStore.subscribe((state, prev) => {
    if (state.sessionId !== prev.sessionId) {
      void queryClient.cancelQueries();
      // Never defer clearing: the next session may populate its cache before cancellation settles.
      queryClient.clear();
    }
  });
}
