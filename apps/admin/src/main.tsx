import { configureApiClient } from "@superdl/api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import React from "react";
import ReactDOM from "react-dom/client";

import { routeTree } from "./routeTree.gen";
import { authStore } from "./stores/auth";

configureApiClient({
  baseUrl: "",
  getToken: () => authStore.getState().accessToken,
  onUnauthorized: () => {
    authStore.getState().logout();
    if (!window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
  },
});

const queryClient = new QueryClient({
  defaultOptions: {
    // NOC 值班场景:全局 60s 轮询 + 切回标签页即刷新
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
