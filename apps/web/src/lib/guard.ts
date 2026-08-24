import { redirect, type ParsedLocation } from "@tanstack/react-router";

import { authStore } from "../stores/auth";

/** 受保护路由 beforeLoad:未登录跳登录页,携带回跳地址(login 侧白名单校验)。 */
export function requireAuth({ location }: { location: ParsedLocation }) {
  if (!authStore.getState().accessToken) {
    throw redirect({ to: "/login", search: { redirect: location.href } });
  }
}
