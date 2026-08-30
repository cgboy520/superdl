/** hash 锚点滚动:SPA 内 React 晚渲染,原生 hash 跳转会落空,须手动补 scrollIntoView。
 *  help.tsx 的 FAQ 锚点是同构先例(那里有展开动画,独立实现);本 hook 服务
 *  /settings#ssh 与落地页 /#pricing、/#ranking 三类静态锚点。
 *  highlight=true 时给目标元素 2s 主题色描边(深链定位反馈)。 */

import { colorPrimary } from "@superdl/ui";
import { useRouterState } from "@tanstack/react-router";
import { useEffect } from "react";

export function useHashScroll({ highlight = false }: { highlight?: boolean } = {}) {
  const hash = useRouterState({ select: (s) => s.location.hash });
  useEffect(() => {
    if (!hash) return;
    const id = hash.replace(/^#/, "");
    let unhighlight: ReturnType<typeof setTimeout> | undefined;
    // 等目标渲染稳定后再滚(同 help.tsx 的 50ms 口径)
    const timer = setTimeout(() => {
      const el = document.getElementById(id);
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth" });
      if (highlight) {
        const prev = el.style.boxShadow;
        el.style.boxShadow = `0 0 0 2px ${colorPrimary}`;
        unhighlight = setTimeout(() => {
          el.style.boxShadow = prev;
        }, 2_000);
      }
    }, 50);
    return () => {
      clearTimeout(timer);
      if (unhighlight) clearTimeout(unhighlight);
    };
  }, [hash, highlight]);
}
