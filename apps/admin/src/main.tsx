import { configureApiClient, requestAdminTokenRefresh } from "@superdl/api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import React from "react";
import ReactDOM from "react-dom/client";

import "./global.css";
import "./i18n";
import { routeTree } from "./routeTree.gen";
import { authStore, readAdminToken } from "./stores/auth";

configureApiClient({
  baseUrl: "",
  // 读 localStorage 而非 store 快照:别的标签页刚续期的 token 立即生效
  getToken: () => readAdminToken(),
  // 静默续期(Web Locks 跨标签页互斥在 mutator 内):滑动换发 access token,
  // 活跃管理员不因 1h TTL 被踢;12h 绝对会话上限在服务端
  refreshToken: async () => {
    const token = readAdminToken();
    if (!token) return false;
    const renewed = await requestAdminTokenRefresh(token);
    if (!renewed) return false;
    authStore.getState().setToken(renewed.access_token);
    return true;
  },
  onUnauthorized: () => {
    authStore.getState().logout();
    if (!window.location.pathname.startsWith("/login")) {
      // 硬跳转到登录页并保留回跳地址(站内路径由登录页白名单校验)
      const returnTo = window.location.pathname + window.location.search;
      window.location.href = `/login?returnTo=${encodeURIComponent(returnTo)}`;
    }
  },
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: true, refetchInterval: 60_000, staleTime: 10_000 },
  },
});

const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
