import type { QueryClient } from "@tanstack/react-query";

import { authStore } from "../stores/auth";

/** access token 的账号位(JWT payload.sub,只解不验)。 */
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

/** 登出(含换号、他标签页同步登出)即清查询缓存:先取消在途查询再 clear;静默续期(同 sub)不清;token 不可解码且值不同按换号处理。 */
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
