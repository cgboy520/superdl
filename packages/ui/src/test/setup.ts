import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// vitest globals:false 时 RTL 不自动 cleanup,多次 render 会叠加到同一 document
afterEach(() => {
  cleanup();
});

// jsdom 无 ResizeObserver(antd ellipsis / EChart 自适应都依赖):行为无关的测试给空实现
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
