/** Cursor list page skeleton: locally controlled search box + 300 ms debounce written back to the URL (replace) + rows deduplicated by key. Shared by the instance and online-service lists. */

import { useDebouncedValue } from "@superdl/ui";
import { useEffect, useMemo, useState } from "react";

export function useCursorList<T, Q extends { data?: { pages: { items: T[] }[] } | undefined }>({
  urlQ,
  commitQ,
  usePages,
  keyOf,
}: {
  /** Search term committed in the URL */
  urlQ: string | undefined;
  /** Write the debounced term back to the URL; stabilised by the caller with useCallback (carrying the route and existing filters) */
  commitQ: (q: string | undefined) => void;
  /** Cursor pagination hook (instance / service list), fed the debounced term */
  usePages: (name: string | undefined) => Q;
  /** Row dedup key (the first page replaced by polling may briefly overlap old pages, the first page wins) */
  keyOf: (row: T) => string;
}) {
  const [keyword, setKeyword] = useState(urlQ ?? "");
  const debouncedKeyword = useDebouncedValue(keyword, 300);
  const deferredQ = debouncedKeyword.trim() || undefined;
  const pagesQ = usePages(deferredQ);

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
