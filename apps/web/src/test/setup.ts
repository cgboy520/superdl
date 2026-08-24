/**
 * vitest 全局 setup(jsdom):
 * - jest-dom 断言扩展(toBeInTheDocument/toBeDisabled 等)
 * - i18n 钉 zh-CN:jsdom 的 navigator.language 是 en-US,不钉语言断言会随环境漂移
 * - antd 在 jsdom 缺的浏览器 API(matchMedia / ResizeObserver)打桩
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import i18n from "../i18n";

// vitest 未开 globals,RTL 的自动 cleanup 不生效,必须手动挂(否则跨用例 DOM 叠加)
afterEach(() => cleanup());

if (!i18n.isInitialized) {
  await new Promise<void>((resolve) => i18n.on("initialized", () => resolve()));
}
await i18n.changeLanguage("zh-CN");

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

class ResizeObserverStub implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
if (!window.ResizeObserver) {
  Object.defineProperty(window, "ResizeObserver", { writable: true, value: ResizeObserverStub });
}
