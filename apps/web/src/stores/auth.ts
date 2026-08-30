/**
 * 认证状态(只存 access token 与登录态,用户资料走 TanStack Query)。
 *
 * refresh token 不落 JS 可达面:HttpOnly Cookie(同源 SameSite=Strict)由浏览器托管,
 * 续期经 api-client 的 401 拦截静默完成。localStorage 只留短 TTL 的 access token,
 * 它是跨标签页的单一事实源:请求路径一律经 readAccessToken() 读它,store 只驱动渲染。
 */

import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";

const TOKEN_KEY = "superdl.web.accessToken";

interface AuthState {
  accessToken: string | null;
  login: (accessToken: string) => void;
  logout: () => void;
}

export function readAccessToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export const authStore = createStore<AuthState>()((set) => ({
  accessToken: readAccessToken(),
  login: (accessToken) => {
    localStorage.setItem(TOKEN_KEY, accessToken);
    set({ accessToken });
  },
  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    set({ accessToken: null });
  },
}));

// 别的标签页续期/登出后同步本页登录态(storage 事件只在其他标签页触发)
window.addEventListener("storage", (e) => {
  if (e.key !== null && e.key !== TOKEN_KEY) return;
  authStore.setState({ accessToken: readAccessToken() });
});

export function useIsLoggedIn(): boolean {
  return useStore(authStore, (s) => s.accessToken !== null);
}
