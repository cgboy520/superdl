/** Auth state: the access token persists in localStorage and syncs across tabs through the storage event. */

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

window.addEventListener("storage", (e) => {
  if (e.key !== null && e.key !== TOKEN_KEY) return;
  authStore.setState({ accessToken: readAccessToken() });
});

export function useIsLoggedIn(): boolean {
  return useStore(authStore, (s) => s.accessToken !== null);
}
