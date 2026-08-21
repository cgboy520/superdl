/**
 * 认证状态(客户端状态极薄:只存 token 与登录态,用户资料走 TanStack Query)。
 *
 * localStorage 是跨标签页的单一事实源:请求路径一律经 readTokens() 读它,
 * store 只驱动渲染。后端 refresh 一次性消费且「重放即撤销全部会话」,
 * 拿标签页内的陈旧副本去续期会把用户全线登出。
 */

import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";

const TOKEN_KEY = "superdl.web.accessToken";
const REFRESH_KEY = "superdl.web.refreshToken";

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  login: (accessToken: string, refreshToken: string) => void;
  logout: () => void;
}

export function readTokens(): { accessToken: string | null; refreshToken: string | null } {
  return {
    accessToken: localStorage.getItem(TOKEN_KEY),
    refreshToken: localStorage.getItem(REFRESH_KEY),
  };
}

export const authStore = createStore<AuthState>()((set) => ({
  ...readTokens(),
  login: (accessToken, refreshToken) => {
    localStorage.setItem(TOKEN_KEY, accessToken);
    localStorage.setItem(REFRESH_KEY, refreshToken);
    set({ accessToken, refreshToken });
  },
  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    set({ accessToken: null, refreshToken: null });
  },
}));

// 别的标签页续期/登出后同步本页登录态(storage 事件只在其他标签页触发)
window.addEventListener("storage", (e) => {
  if (e.key !== null && e.key !== TOKEN_KEY && e.key !== REFRESH_KEY) return;
  authStore.setState(readTokens());
});

export function useIsLoggedIn(): boolean {
  return useStore(authStore, (s) => s.accessToken !== null);
}
