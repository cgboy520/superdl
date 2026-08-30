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
  // 读 localStorage 而非 store 快照:别的标签页刚续期的 token 立即生效
  getToken: () => readAdminToken(),
  // 静默续期(Web Locks 跨标签页互斥在 mutator 内):滑动换发 access token,12h 绝对会话上限在服务端
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

// 底色三处拷贝收敛:global.css 的 var(--admin-bg)/var(--admin-chart-neutral) 在运行时由这里注入,
// 单一事实源是 packages/ui tokens.ts adminColors(index.html 的静态防 FOUC 值需手动同步)
document.documentElement.style.setProperty("--admin-bg", adminColors.bgBase);
document.documentElement.style.setProperty("--admin-chart-neutral", adminColors.chartNeutral);
// 命令面板选中行底色(global.css .command-palette):主色经变量注入,CSS 不硬编码
document.documentElement.style.setProperty("--admin-accent", adminColors.dataAccent);

const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  // 慢网切换菜单的等待反馈:beforeLoad(/me)未完成时居中 Spin,不再白屏无响应
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
      {/* 全局动效策略:尊重系统减弱动态效果设置(NOC 端仅保留状态变更淡入) */}
      <MotionConfig reducedMotion="user">
        <RouterProvider router={router} />
      </MotionConfig>
    </QueryClientProvider>
  </React.StrictMode>,
);
