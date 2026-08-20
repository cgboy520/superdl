import { configureApiClient, requestTokenRefresh } from "@superdl/api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import React from "react";
import ReactDOM from "react-dom/client";

import { routeTree } from "./routeTree.gen";
import { authStore } from "./stores/auth";
import "./styles.css";

configureApiClient({
  baseUrl: "",
  getToken: () => authStore.getState().accessToken,
  refreshToken: async () => {
    const rt = authStore.getState().refreshToken;
    if (!rt) return false;
    const pair = await requestTokenRefresh(rt);
    if (!pair) return false;
    authStore.getState().login(pair.access_token, pair.refresh_token);
    return true;
  },
  onUnauthorized: () => {
    authStore.getState().logout();
    // 会话失效时回登录页并带回跳
    if (!window.location.pathname.startsWith("/login")) {
      window.location.href = `/login?redirect=${encodeURIComponent(window.location.pathname)}`;
    }
  },
});

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

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
