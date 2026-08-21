/**
 * 管理端认证状态。与用户端 token 存储键隔离(两端 JWT audience 不同,不可互用)。
 * 客户端状态极薄:token + 管理员身份(角色驱动菜单/按钮可见性)。
 */

import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";

const TOKEN_KEY = "superdl.admin.accessToken";
const ADMIN_KEY = "superdl.admin.info";

export interface AdminInfo {
  id: number;
  username: string;
  role: string; // admin / ops / finance / readonly
}

interface AuthState {
  accessToken: string | null;
  admin: AdminInfo | null;
  login: (accessToken: string, admin: AdminInfo) => void;
  logout: () => void;
}

function loadAdmin(): AdminInfo | null {
  try {
    const raw = localStorage.getItem(ADMIN_KEY);
    return raw ? (JSON.parse(raw) as AdminInfo) : null;
  } catch {
    return null;
  }
}

export const authStore = createStore<AuthState>()((set) => ({
  accessToken: localStorage.getItem(TOKEN_KEY),
  admin: loadAdmin(),
  login: (accessToken, admin) => {
    localStorage.setItem(TOKEN_KEY, accessToken);
    localStorage.setItem(ADMIN_KEY, JSON.stringify(admin));
    set({ accessToken, admin });
  },
  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ADMIN_KEY);
    set({ accessToken: null, admin: null });
  },
}));

export function useAuth(): AuthState {
  return useStore(authStore);
}

export function useAdminRole(): string {
  return useStore(authStore, (s) => s.admin?.role ?? "readonly");
}

/** 资源写权限(SKU/实例/租户):admin·ops */
export function canWriteOps(role: string): boolean {
  return role === "admin" || role === "ops";
}

/** 财务写权限(发起/复核调账):admin·finance */
export function canWriteFinance(role: string): boolean {
  return role === "admin" || role === "finance";
}
