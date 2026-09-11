import { defineConfig } from "vitest/config";

// 独立于 vite.config.ts,不挂 TanStack Router 插件
export default defineConfig({
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["src/test/setup.ts"],
  },
});
