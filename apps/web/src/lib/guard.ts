import { redirect } from "@tanstack/react-router";

import { authStore } from "../stores/auth";

/** 受保护路由 beforeLoad:未登录跳登录页。 */
export function requireAuth() {
  if (!authStore.getState().accessToken) {
    throw redirect({ to: "/login" });
  }
}
