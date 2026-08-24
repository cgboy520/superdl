import { configureApiClient, requestTokenRefresh } from "@superdl/api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import React from "react";
import ReactDOM from "react-dom/client";

import { routeTree } from "./routeTree.gen";
import { authStore, readTokens } from "./stores/auth";
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
  // 请求路径读 localStorage 而非 store 快照:别的标签页刚续期的 token 立即生效
  getToken: () => readTokens().accessToken,
  refreshToken: async () => {
    const rt = readTokens().refreshToken;
    if (!rt) return false;
    const pair = await requestTokenRefresh(rt);
    if (!pair) return false;
    authStore.getState().login(pair.access_token, pair.refresh_token);
    return true;
  },
  onUnauthorized: () => {
    authStore.getState().logout();
    const { pathname, href } = router.state.location;
    if (!pathname.startsWith("/login")) {
      // 回跳地址带完整 query/hash(如 /instances?tab=events),走路由跳转而非整页刷新
      void router.navigate({ to: "/login", search: { redirect: href } });
    }
  },
});

// token 变化(登出/换号/他标签页同步)即清查询缓存:先取消在途查询再 clear,
// 否则换号登录会先渲染上一个账号的余额与实例
authStore.subscribe((state, prev) => {
  if (prev.accessToken && prev.accessToken !== state.accessToken) {
    void queryClient.cancelQueries().then(() => queryClient.clear());
  }
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
