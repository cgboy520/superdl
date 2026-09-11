/** 列表页最近一次筛选态(按列表路径记),供详情页「返回列表」链接带回原筛选;不入 URL,sessionStorage 持久化到本标签页。 */

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
    // 剥离 undefined,避免 JSON 序列化后键残留
    const clean: ListSearch = {};
    for (const [k, v] of Object.entries(search)) if (v !== undefined) clean[k] = v;
    const byPath = { ...get().byPath, [path]: clean };
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(byPath));
    } catch {
      /* 存储不可用时只留内存 */
    }
    set({ byPath });
  },
}));

/** 读取某列表路径最近的筛选态(无记录返回空对象)。 */
export function useRememberedListSearch(path: string): ListSearch {
  return useStore(listSearchStore, (s) => s.byPath[path] ?? {});
}
