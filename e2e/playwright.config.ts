import { defineConfig } from "@playwright/test";

/** 冒烟用例:需 API(8000,已迁移 + seed)在跑(`cd apps/api && uv run uvicorn app.main:app --port 8000`);web(5173)由本配置拉起;SUPERDL_ADMIN_E2E=1 时再拉起 admin(5174)。 */
const adminE2E = Boolean(process.env.SUPERDL_ADMIN_E2E);

export default defineConfig({
  testDir: "./tests",
  // 重链路用例等 worker+reconciler(周期 30s)推进,统一 5min
  timeout: 300_000,
  // CI 给一次重试;本地 0
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: "http://localhost:5173",
    locale: "zh-CN", // 钉死语言,中文定位器依赖
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "pnpm --filter web dev",
      url: "http://localhost:5173",
      reuseExistingServer: true,
      cwd: "..",
      timeout: 60_000,
    },
    ...(adminE2E
      ? [
          {
            command: "pnpm --filter admin dev",
            url: "http://localhost:5174",
            reuseExistingServer: true,
            cwd: "..",
            timeout: 60_000,
          },
        ]
      : []),
  ],
});
