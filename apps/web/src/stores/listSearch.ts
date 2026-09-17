/** Last filter state of each list page (keyed by list path) so the detail page's "back to list" restores it; not in the URL, persisted in sessionStorage per tab. */

import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";

export type ListSearch = Record<string, string | number | boolean | undefined>;

const STORAGE_KEY = "superdl.web.listSearch";

function load(): Record<string, ListSearch> {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Record<string, ListSearch>) : {};
  } catch {
    return {};
  }
}

export const listSearchStore = createStore<{
  byPath: Record<string, ListSearch>;
  remember: (path: string, search: ListSearch) => void;
}>()((set, get) => ({
  byPath: load(),
  remember: (path, search) => {
    const clean: ListSearch = {};
    for (const [k, v] of Object.entries(search)) if (v !== undefined) clean[k] = v;
    const byPath = { ...get().byPath, [path]: clean };
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(byPath));
    } catch {
      /* ignored */
    }
    set({ byPath });
  },
}));

/** Stable empty object returned without a record. */
const EMPTY: ListSearch = Object.freeze({});

/** Read the last filter state of a list path (a stable empty object without a record). */
export function useRememberedListSearch(path: string): ListSearch {
  return useStore(listSearchStore, (s) => s.byPath[path] ?? EMPTY);
}
