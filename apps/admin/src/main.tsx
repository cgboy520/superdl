import { configureApiClient, requestAdminTokenRefresh } from "@superdl/api-client";
import { adminColors } from "@superdl/ui";
import { QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { Spin } from "antd";
import { MotionConfig } from "motion/react";
import React from "react";
import ReactDOM from "react-dom/client";

import "@superdl/ui/base.css";
import "./global.css";
import i18n from "./i18n";
import { queryClient } from "./lib/queryClient";
import { routeTree } from "./routeTree.gen";
import { authStore, readAdminToken } from "./stores/auth";

configureApiClient({
  baseUrl: "",
  getLocale: () => i18n.language,
  getToken: () => readAdminToken(),
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
      const returnTo = window.location.pathname + window.location.search;
      window.location.href = `/login?returnTo=${encodeURIComponent(returnTo)}`;
    }
  },
});

document.documentElement.style.setProperty("--admin-bg", adminColors.bgBase);
document.documentElement.style.setProperty("--admin-chart-neutral", adminColors.chartNeutral);
document.documentElement.style.setProperty("--admin-accent", adminColors.dataAccent);
document.documentElement.style.setProperty("--sdl-color-primary", adminColors.dataAccent);

const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  defaultPendingComponent: () => (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Spin size="large" />
    </div>
  ),
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root element missing");
ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <MotionConfig reducedMotion="user">
        <RouterProvider router={router} />
      </MotionConfig>
    </QueryClientProvider>
  </React.StrictMode>,
);
