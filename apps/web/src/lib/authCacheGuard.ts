import type { QueryClient } from "@tanstack/react-query";

import { authStore } from "../stores/auth";

/**
 * 登出(含换号的登出步、他标签页同步登出)即清查询缓存:先取消在途查询再 clear,
 * 否则换号登录会先渲染上一个账号的余额与实例。
 * 静默续期(access token A→A')不清:续期每小时发生一次,清缓存会让全站每小时
 * 白屏一次(列表回骨架屏、余额变 —、图表清空、表格重置,且跨标签同步放大)。
 */
export function setupAuthCacheGuard(queryClient: QueryClient): () => void {
  return authStore.subscribe((state, prev) => {
    if (prev.accessToken && state.accessToken === null) {
      void queryClient.cancelQueries().then(() => queryClient.clear());
    }
  });
}
