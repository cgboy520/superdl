import { configureApiClient, requestAdminTokenRefresh } from "@superdl/api-client";
import { adminColors } from "@superdl/ui";
import { QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { Spin } from "antd";
import { MotionConfig } from "motion/react";
import React from "react";
import ReactDOM from "react-dom/client";

import "./global.css";
import "./i18n";
import { queryClient } from "./lib/queryClient";
import { routeTree } from "./routeTree.gen";
import { authStore, readAdminToken } from "./stores/auth";

configureApiClient({
  baseUrl: "",
  // 读 localStorage(跨标签页续期即时生效)
  getToken: () => readAdminToken(),
  // 静默续期(Web Locks 互斥在 mutator 内)
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
      // 硬跳转登录页并保留回跳地址
      const returnTo = window.location.pathname + window.location.search;
      window.location.href = `/login?returnTo=${encodeURIComponent(returnTo)}`;
    }
  },
});

// global.css 的 var(--admin-bg)/var(--admin-chart-neutral) 由这里注入,取 packages/ui tokens.ts adminColors(index.html 静态值手动同步)
document.documentElement.style.setProperty("--admin-bg", adminColors.bgBase);
document.documentElement.style.setProperty("--admin-chart-neutral", adminColors.chartNeutral);
// 命令面板选中行底色(global.css .command-palette)经变量注入
document.documentElement.style.setProperty("--admin-accent", adminColors.dataAccent);

const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  // beforeLoad(/me) 等待态
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

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* 尊重系统减弱动态效果设置 */}
      <MotionConfig reducedMotion="user">
        <RouterProvider router={router} />
      </MotionConfig>
    </QueryClientProvider>
  </React.StrictMode>,
);
