import { defineConfig } from "vitest/config";

// 独立于 vite.config.ts:测试不需要 TanStack Router 插件(会重生成 routeTree);
// tsx 由 esbuild 按 tsconfig jsx=react-jsx 自动运行时转换
export default defineConfig({
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["src/test/setup.ts"],
  },
});
