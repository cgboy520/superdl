/** Admin auth state: token + admin identity; storage keys isolated from the user console. */

import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";

import { queryClient } from "../lib/queryClient";

const TOKEN_KEY = "superdl.admin.accessToken";

export interface AdminInfo {
  id: number;
  username: string;
  role: string;
}

interface AuthState {
  accessToken: string | null;
  admin: AdminInfo | null;
  login: (accessToken: string, admin: AdminInfo) => void;
  /** Silent renewal: swaps the token only, identity unchanged. */
  setToken: (accessToken: string) => void;
  /** Calibrate the local identity from the /me response; token unchanged. */
  setAdmin: (admin: AdminInfo) => void;
  logout: () => void;
}

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
    queryClient.clear();
    set({ accessToken: null, admin: null });
  },
}));

window.addEventListener("storage", (e) => {
  if (e.key !== null && e.key !== TOKEN_KEY) return;
  authStore.setState({ accessToken: readAdminToken() });
});

export function useAuth(): AuthState {
  return useStore(authStore);
}

/** Request path reads localStorage (cross-tab renewal takes effect at once). */
export function readAdminToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function useAdminRole(): string {
  return useStore(authStore, (s) => s.admin?.role ?? "readonly");
}

/** Resource write permission (SKU/instances/tenants): admin·ops */
export function canWriteOps(role: string): boolean {
  return role === "admin" || role === "ops";
}

/** Finance write permission (create/review adjustments): admin·finance */
export function canWriteFinance(role: string): boolean {
  return role === "admin" || role === "finance";
}

/** Invoice read permission (backend require_roles("finance")): admin·finance; a separate rule from canWriteFinance. */
export function canReadInvoices(role: string): boolean {
  return role === "admin" || role === "finance";
}
