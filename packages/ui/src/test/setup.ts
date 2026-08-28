import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// vitest globals:false 时 RTL 不自动 cleanup,多次 render 会叠加到同一 document
afterEach(() => {
  cleanup();
});
