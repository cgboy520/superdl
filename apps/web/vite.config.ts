import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [tanstackRouter({ target: "react", autoCodeSplitting: true }), react()],
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    // 懒路由用例的 findBy 等待上限 10s,单条用例超时必须大于它
    testTimeout: 15_000,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
