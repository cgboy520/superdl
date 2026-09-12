import { configureApiClient, requestTokenRefresh } from "@superdl/api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import React from "react";
import ReactDOM from "react-dom/client";

import { routeTree } from "./routeTree.gen";
import { setupAuthCacheGuard } from "./lib/authCacheGuard";
import { authStore, readAccessToken } from "./stores/auth";
import "./i18n";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: true, staleTime: 10_000 },
  },
});

const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

configureApiClient({
  baseUrl: "",
  // 请求路径读 localStorage 而非 store 快照
  getToken: () => readAccessToken(),
  refreshToken: async () => {
    // refresh 走 HttpOnly Cookie,JS 只接新 access token
    const pair = await requestTokenRefresh();
    if (!pair) return false;
    authStore.getState().login(pair.access_token);
    return true;
  },
  onUnauthorized: () => {
    authStore.getState().logout();
    const { pathname, href } = router.state.location;
    if (!pathname.startsWith("/login")) {
      // 回跳地址带完整 query/hash,走路由跳转
      void router.navigate({ to: "/login", search: { redirect: href } });
    }
  },
});

// 登出即清查询缓存(静默续期不清,见 authCacheGuard)
setupAuthCacheGuard(queryClient);

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root element missing");
ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
