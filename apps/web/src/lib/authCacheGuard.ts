import type { QueryClient } from "@tanstack/react-query";

import { authStore } from "../stores/auth";

/** access token 的账号位(JWT payload.sub,只解不验:这里不做鉴权,只是缓存卫生)。 */
function accountOf(token: string): string | null {
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const payload: unknown = JSON.parse(
      atob(part.replace(/-/g, "+").replace(/_/g, "/")),
    );
    const sub = (payload as { sub?: unknown }).sub;
    return typeof sub === "string" ? sub : null;
  } catch {
    return null;
  }
}

/**
 * 登出(含换号、他标签页同步登出)即清查询缓存:必须先取消在途查询再 clear,
 * 否则换号登录会先渲染上一个账号的余额与实例。
 * 静默续期(同账号 access token A→A')不清,否则全站每小时回一次骨架屏。
 * 判定看账号位(sub)而非 token 值:换号登录 A→B 也是「登出 A」;token 不可解码
 * 且值不同按换号处理(安全方向:拿不准就清)。
 */
export function setupAuthCacheGuard(queryClient: QueryClient): () => void {
  return authStore.subscribe((state, prev) => {
    const loggedOut = prev.accessToken !== null && state.accessToken === null;
    let accountSwitched = false;
    if (prev.accessToken !== null && state.accessToken !== null) {
      const before = accountOf(prev.accessToken);
      const after = accountOf(state.accessToken);
      accountSwitched =
        before === null || after === null
          ? prev.accessToken !== state.accessToken
          : before !== after;
    }
    if (loggedOut || accountSwitched) {
      void queryClient.cancelQueries().then(() => queryClient.clear());
    }
  });
}
