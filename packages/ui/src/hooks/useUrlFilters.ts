/** Generic URL filter state check and clear (no router dependency): the caller passes the current search, the filter keys and the write-back function. */

import { useCallback, useMemo } from "react";

type FilterValue = string | number | boolean | undefined;

export function useUrlFilters<S extends Record<string, FilterValue>>({
  search,
  keys,
  commit,
}: {
  search: S;
  /** Keys taking part in the "has filters / clear filters" decision */
  keys: readonly (keyof S)[];
  /** Write back to the URL (the caller uses navigate({ search: prev => ({ ...prev, ...patch }), replace: true })) */
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
