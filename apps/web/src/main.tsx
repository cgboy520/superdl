import { configureApiClient, requestTokenRefresh } from "@superdl/api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import React from "react";
import ReactDOM from "react-dom/client";

import { routeTree } from "./routeTree.gen";
import { setupAuthCacheGuard } from "./lib/authCacheGuard";
import { authStore, readAccessToken } from "./stores/auth";
import "./i18n";
import "@superdl/ui/base.css";
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
  getToken: () => readAccessToken(),
  refreshToken: async () => {
    const pair = await requestTokenRefresh();
    if (!pair) return false;
    authStore.getState().login(pair.access_token);
    return true;
  },
  onUnauthorized: () => {
    authStore.getState().logout();
    const { pathname, href } = router.state.location;
    if (!pathname.startsWith("/login")) {
      void router.navigate({ to: "/login", search: { redirect: href } });
    }
  },
});

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
