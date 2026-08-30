/** QueryClient 单例:main.tsx 挂 Provider,_app.tsx beforeLoad 经 ensureQueryData 复用 /me 缓存。 */

import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    // 禁设全局轮询,否则 infinite 列表会被全页重拉、表单页会被刷新覆盖;要轮询的查询各自声明 refetchInterval
    queries: { retry: 1, refetchOnWindowFocus: true, staleTime: 10_000 },
  },
});
