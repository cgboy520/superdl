/** jsdom gap fillers: browser APIs antd components need (unimplemented in jsdom); money formatting pinned to CNY (production reads platform-config deployment). */
import { setFallbackCurrency } from "@superdl/ui";

setFallbackCurrency("CNY");

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (typeof globalThis.ResizeObserver === "undefined") {
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
}

if (typeof window.matchMedia !== "function") {
  window.matchMedia = (query: string): MediaQueryList => {
    // Plain object: the legacy listener pair stays for libraries that still call it.
    const stub = {
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    };
    return stub;
  };
}
