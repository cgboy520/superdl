/** vitest global setup (jsdom): jest-dom matchers; i18n pinned to zh-CN; matchMedia / ResizeObserver stubs for antd. */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import { setFallbackCurrency } from "@superdl/ui";

import i18n from "../i18n";

// Fixtures assert zh-CN yen formatting; production gets the currency from /site-config.
setFallbackCurrency("CNY");

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
