/** URL 筛选态的通用判定与清除(不依赖路由库):调用方传入当前 search、参与筛选的键与回写函数。 */

import { useCallback, useMemo } from "react";

type FilterValue = string | number | boolean | undefined;

export function useUrlFilters<S extends Record<string, FilterValue>>({
  search,
  keys,
  commit,
}: {
  search: S;
  /** 参与「有筛选 / 清除筛选」判定的键 */
  keys: readonly (keyof S)[];
  /** 回写 URL(调用方用 navigate({ search: prev => ({ ...prev, ...patch }), replace: true })) */
  commit: (patch: Partial<S>) => void;
}): { hasFilter: boolean; clear: () => void; set: (patch: Partial<S>) => void } {
  const hasFilter = useMemo(
    () => keys.some((k) => search[k] !== undefined && search[k] !== "" && search[k] !== false),
    [keys, search],
  );
  const clear = useCallback(() => {
    const patch: Partial<S> = {};
    for (const k of keys) patch[k] = undefined;
    commit(patch);
  }, [keys, commit]);
  return { hasFilter, clear, set: commit };
}
