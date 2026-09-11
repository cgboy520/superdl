/** 当前时刻按间隔刷新(倒计时/到期天数用);intervalMs<=0 或 undefined 不 tick。 */

import { useEffect, useState } from "react";

export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (intervalMs <= 0) return;
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}
