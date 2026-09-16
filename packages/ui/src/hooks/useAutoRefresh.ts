/** Page-level auto-refresh switch used with PageHeader.freshness. paused → refetchInterval=false, resumed → poll by intervalMs. */

import { useCallback, useState } from "react";

export function useAutoRefresh(intervalMs: number): {
  paused: boolean;
  toggle: () => void;
  /** Fed straight into react-query's refetchInterval */
  refetchInterval: number | false;
  intervalMs: number;
} {
  const [paused, setPaused] = useState(false);
  const toggle = useCallback(() => setPaused((p) => !p), []);
  return { paused, toggle, refetchInterval: paused ? false : intervalMs, intervalMs };
}
