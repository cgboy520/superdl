/** Theme mode (light/dark): persisted in localStorage("superdl.theme"), initial = stored value ?? prefers-color-scheme; the index.html inline script uses the same decision to pre-set background / color-scheme. */

import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";

export type ThemeMode = "light" | "dark";

const THEME_KEY = "superdl.theme";

function initialMode(): ThemeMode {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export const themeStore = createStore<{ mode: ThemeMode; toggle: () => void }>()((set, get) => ({
  mode: initialMode(),
  toggle: () => {
    const mode: ThemeMode = get().mode === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, mode);
    set({ mode });
  },
}));

export function useThemeMode(): ThemeMode {
  return useStore(themeStore, (s) => s.mode);
}

export function useThemeToggle(): () => void {
  return useStore(themeStore, (s) => s.toggle);
}
