/** 管理端认证状态:token + 管理员身份;存储键与用户端隔离。 */

import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";

import { queryClient } from "../lib/queryClient";

const TOKEN_KEY = "superdl.admin.accessToken";

export interface AdminInfo {
  id: number;
  username: string;
  role: string; // admin / ops / finance / readonly
}

interface AuthState {
  accessToken: string | null;
  admin: AdminInfo | null;
  login: (accessToken: string, admin: AdminInfo) => void;
  /** 静默续期换发:只换 token,身份不变。 */
  setToken: (accessToken: string) => void;
  /** 以 /me 响应校准本地身份;token 不变。 */
  setAdmin: (admin: AdminInfo) => void;
  logout: () => void;
}

// 管理员身份只放内存,进入受保护路由经 /me 校准(_app.tsx beforeLoad)
export const authStore = createStore<AuthState>()((set) => ({
  accessToken: localStorage.getItem(TOKEN_KEY),
  admin: null,
  login: (accessToken, admin) => {
    localStorage.setItem(TOKEN_KEY, accessToken);
    set({ accessToken, admin });
  },
  setToken: (accessToken) => {
    localStorage.setItem(TOKEN_KEY, accessToken);
    set({ accessToken });
  },
  setAdmin: (admin) => set({ admin }),
  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    // 清查询缓存
    queryClient.clear();
    set({ accessToken: null, admin: null });
  },
}));

export function useAuth(): AuthState {
  return useStore(authStore);
}

/** 请求路径读 localStorage(跨标签页续期即时生效)。 */
export function readAdminToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
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

/** 发票读权限(后端 require_roles("finance")):admin·finance;与 canWriteFinance 是两条规则。 */
export function canReadInvoices(role: string): boolean {
  return role === "admin" || role === "finance";
}
