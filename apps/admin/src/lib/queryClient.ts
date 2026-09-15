/** QueryClient 单例(main.tsx Provider;_app.tsx beforeLoad 复用 /me 缓存)。 */

import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: true, staleTime: 10_000 },
  },
});
