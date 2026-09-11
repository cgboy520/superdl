/** 页面级自动刷新开关:配合 PageHeader.freshness 使用。paused 时 refetchInterval=false,恢复时按 intervalMs 轮询。 */

import { useCallback, useState } from "react";

export function useAutoRefresh(intervalMs: number): {
  paused: boolean;
  toggle: () => void;
  /** 直接喂给 react-query 的 refetchInterval */
  refetchInterval: number | false;
  intervalMs: number;
} {
  const [paused, setPaused] = useState(false);
  const toggle = useCallback(() => setPaused((p) => !p), []);
  return { paused, toggle, refetchInterval: paused ? false : intervalMs, intervalMs };
}
