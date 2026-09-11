/** 控制台全局快捷键(挂在 _console 布局,与 ⌘K 并存):`/` 聚焦当前页第一个 data-search-input;`g`+序列 500ms 内两键导航(g d→/dashboard,g i→/instances,g s→/services,g b→/billing,g m→/market);输入框聚焦时不触发。 */

import { useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";

/** 序列键间隔上限 */
const SEQ_TIMEOUT_MS = 500;

function isTypingTarget(el: Element | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
}

export function useGlobalHotkeys() {
  const navigate = useNavigate();
  useEffect(() => {
    // 最近一次 `g` 按下的时间戳(0 = 无挂起序列)
    let pendingG = 0;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTypingTarget(document.activeElement)) {
        pendingG = 0;
        return;
      }
      if (e.key === "/") {
        const el = document.querySelector<HTMLElement>("[data-search-input]");
        if (el) {
          e.preventDefault();
          el.focus();
        }
        pendingG = 0;
        return;
      }
      const now = Date.now();
      if (e.key === "g") {
        pendingG = now;
        return;
      }
      if (pendingG > 0 && now - pendingG <= SEQ_TIMEOUT_MS) {
        pendingG = 0;
        switch (e.key) {
          case "d":
            e.preventDefault();
            void navigate({ to: "/dashboard" });
            return;
          case "i":
            e.preventDefault();
            void navigate({ to: "/instances" });
            return;
          case "s":
            e.preventDefault();
            void navigate({ to: "/services" });
            return;
          case "b":
            e.preventDefault();
            void navigate({ to: "/billing" });
            return;
          case "m":
            e.preventDefault();
            void navigate({ to: "/market" });
            return;
          default:
            return;
        }
      }
      pendingG = 0;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);
}
