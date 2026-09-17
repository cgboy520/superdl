/** QueryClient singleton (main.tsx Provider; _app.tsx beforeLoad reuses the /me cache). */

import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: true, staleTime: 10_000 },
  },
});
