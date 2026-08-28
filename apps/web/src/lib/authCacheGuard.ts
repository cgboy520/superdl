import type { QueryClient } from "@tanstack/react-query";

import { authStore } from "../stores/auth";

/**
 * 登出(含换号、他标签页同步登出)即清查询缓存:必须先取消在途查询再 clear,
 * 否则换号登录会先渲染上一个账号的余额与实例。
 * 静默续期(access token A→A')不清,否则全站每小时回一次骨架屏。
 */
export function setupAuthCacheGuard(queryClient: QueryClient): () => void {
  return authStore.subscribe((state, prev) => {
    if (prev.accessToken && state.accessToken === null) {
      void queryClient.cancelQueries().then(() => queryClient.clear());
    }
  });
}
