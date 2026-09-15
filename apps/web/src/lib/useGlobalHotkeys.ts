import { useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";

/** 序列键间隔上限 */
const SEQ_TIMEOUT_MS = 500;

function isTypingTarget(el: Element | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
}

/** 控制台快捷键:/ 聚焦搜索框,g+i/s/b/m 导航;输入框、文本域或可编辑内容内不触发。 */
export function useGlobalHotkeys() {
  const navigate = useNavigate();
  useEffect(() => {
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
