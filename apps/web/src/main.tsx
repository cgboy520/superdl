import { configureApiClient, requestTokenRefresh } from "@superdl/api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import React from "react";
import ReactDOM from "react-dom/client";

import { routeTree } from "./routeTree.gen";
import { setupAuthCacheGuard } from "./lib/authCacheGuard";
import { authStore, readAuthSession } from "./stores/auth";
import i18n from "./i18n";
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
  getLocale: () => i18n.language,
  getSession: readAuthSession,
  refreshToken: async (sessionId) => {
    const pair = await requestTokenRefresh();
    // refreshOnce already holds the shared auth lock.
    return pair !== null && authStore.getState().renewTokenUnderLock(pair.access_token, sessionId);
  },
  onUnauthorized: async (sessionId) => {
    if (!(await authStore.getState().logout(sessionId))) return;
    const { pathname, href } = router.state.location;
    if (!pathname.startsWith("/login")) {
      await router.navigate({ to: "/login", search: { redirect: href } });
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
