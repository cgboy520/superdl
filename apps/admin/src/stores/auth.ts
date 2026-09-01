/**
 * 管理端认证状态。与用户端 token 存储键隔离(两端 JWT audience 不同,不可互用)。
 * 客户端状态极薄:token + 管理员身份(角色驱动菜单/按钮可见性)。
 */

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
  /** 以服务端 /me 响应校准本地身份(角色只信服务端);token 不变。 */
  setAdmin: (admin: AdminInfo) => void;
  logout: () => void;
}

// 管理员身份只放内存:每次进入受保护路由都经 /me 校准(_app.tsx beforeLoad),落盘既无人读也会过期
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
    // 清掉查询缓存:换号重登后不得看到上一个账号的角色/数据残影
    queryClient.clear();
    set({ accessToken: null, admin: null });
  },
}));

export function useAuth(): AuthState {
  return useStore(authStore);
}

/** 请求路径读 localStorage 而非 store 快照:别的标签页刚续期的 token 立即生效。 */
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

/** 发票读权限(后端 /invoices 与 /invoices/export 均 require_roles("finance")):admin·finance。
 *  与 canWriteFinance 当前同集合但不是同一条规则:这是抬头/邮箱这份自然人 PII 的出口口径,
 *  不跟着财务写权限走,后端改了读门只动这里。 */
export function canReadInvoices(role: string): boolean {
  return role === "admin" || role === "finance";
}
