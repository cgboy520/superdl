import { useThemeColors } from "@superdl/ui";
import { useRouterState } from "@tanstack/react-router";
import { useEffect } from "react";

/** 滚动至 URL hash 对应元素;highlight=true 时添加 2s 主题色描边。 */
export function useHashScroll({ highlight = false }: { highlight?: boolean } = {}) {
  const hash = useRouterState({ select: (s) => s.location.hash });
  const { primary } = useThemeColors();
  useEffect(() => {
    if (!hash) return;
    const id = hash.replace(/^#/, "");
    let unhighlight: ReturnType<typeof setTimeout> | undefined;
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
