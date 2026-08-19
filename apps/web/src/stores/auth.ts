/**
 * 认证状态(客户端状态极薄:只存 token 与登录态,用户资料走 TanStack Query)。
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

export const authStore = createStore<AuthState>()((set) => ({
  accessToken: localStorage.getItem(TOKEN_KEY),
  refreshToken: localStorage.getItem(REFRESH_KEY),
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

export function useAuth(): AuthState {
  return useStore(authStore);
}

export function useIsLoggedIn(): boolean {
  return useStore(authStore, (s) => s.accessToken !== null);
}
