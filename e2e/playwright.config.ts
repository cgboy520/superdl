import { defineConfig } from "@playwright/test";

/**
 * WP12 冒烟:需要 API(8000,已迁移+seed)与 web dev server(5173)在跑。
 *   cd apps/api && uv run uvicorn app.main:app --port 8000
 *   pnpm --filter web dev
 */
export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "pnpm --filter web dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    cwd: "..",
    timeout: 60_000,
  },
});
