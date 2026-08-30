/** 当前时刻 tick(两端共用):倒计时/到期天数类 UI 用挂载快照会随页面长开而过期,
 *  统一经此 hook 按间隔刷新。intervalMs<=0 或 undefined 时不 tick(静态快照)。
 */

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
