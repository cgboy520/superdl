/** 游标列表页骨架:搜索框本地受控 + 防抖 300ms 回写 URL(replace)+ 分页按键去重行。实例列表与在线服务列表共用。 */

import { useDebouncedValue } from "@superdl/ui";
import { useEffect, useMemo, useState } from "react";

export function useCursorList<T, Q extends { data?: { pages: { items: T[] }[] } | undefined }>({
  urlQ,
  commitQ,
  usePages,
  keyOf,
}: {
  /** URL 里已提交的检索词 */
  urlQ: string | undefined;
  /** 防抖后的检索词回写 URL;由调用方 useCallback 稳定化(带上路由与既有筛选) */
  commitQ: (q: string | undefined) => void;
  /** 游标分页 hook(实例 / 服务列表),入参为防抖后的检索词 */
  usePages: (name: string | undefined) => Q;
  /** 行去重键(轮询替换首页与旧页可能短暂重叠,首页优先) */
  keyOf: (row: T) => string;
}) {
  const [keyword, setKeyword] = useState(urlQ ?? "");
  const debouncedKeyword = useDebouncedValue(keyword, 300);
  const deferredQ = debouncedKeyword.trim() || undefined;
  const pagesQ = usePages(deferredQ);

  // 列表检索入 URL(replace)
  useEffect(() => {
    if ((urlQ ?? "") === (deferredQ ?? "")) return;
    commitQ(deferredQ);
  }, [urlQ, deferredQ, commitQ]);

  const rows = useMemo<T[]>(() => {
    const seen = new Set<string>();
    const out: T[] = [];
    for (const p of pagesQ.data?.pages ?? []) {
      for (const i of p.items) {
        const k = keyOf(i);
        if (seen.has(k)) continue;
        seen.add(k);
        out.push(i);
      }
    }
    return out;
  }, [pagesQ.data, keyOf]);

  return { keyword, setKeyword, rows, ...pagesQ };
}
