/**
 * 管理端认证状态。与用户端 token 存储键隔离(两端 JWT audience 不同,不可互用)。
 */

import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";

const TOKEN_KEY = "superdl.admin.accessToken";
const REFRESH_KEY = "superdl.admin.refreshToken";

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
