/** vitest 全局 setup(jsdom):jest-dom 断言扩展;i18n 钉 zh-CN;antd 依赖的 matchMedia / ResizeObserver 打桩。 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import i18n from "../i18n";

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
if (!("ResizeObserver" in window)) {
  Object.defineProperty(window, "ResizeObserver", { writable: true, value: ResizeObserverStub });
}
