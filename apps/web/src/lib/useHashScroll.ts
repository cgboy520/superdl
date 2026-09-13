/** hash 锚点滚动(手动补 scrollIntoView):服务 /settings#ssh 与落地页 /#pricing、/#ranking;help.tsx 的 FAQ 锚点独立实现。highlight=true 时目标元素 2s 主题色描边。 */

import { useThemeColors } from "@superdl/ui";
import { useRouterState } from "@tanstack/react-router";
import { useEffect } from "react";

export function useHashScroll({ highlight = false }: { highlight?: boolean } = {}) {
  const hash = useRouterState({ select: (s) => s.location.hash });
  const { primary } = useThemeColors();
  useEffect(() => {
    if (!hash) return;
    const id = hash.replace(/^#/, "");
    let unhighlight: ReturnType<typeof setTimeout> | undefined;
    // 等目标渲染稳定后再滚(同 help.tsx 的 50ms)
    const timer = setTimeout(() => {
      const el = document.getElementById(id);
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth" });
      if (highlight) {
        const prev = el.style.boxShadow;
        el.style.boxShadow = `0 0 0 2px ${primary}`;
        unhighlight = setTimeout(() => {
          el.style.boxShadow = prev;
        }, 2_000);
      }
    }, 50);
    return () => {
      clearTimeout(timer);
      if (unhighlight) clearTimeout(unhighlight);
    };
  }, [hash, highlight, primary]);
}
