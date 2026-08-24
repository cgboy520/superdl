import { defineConfig } from "@playwright/test";

/**
 * 冒烟用例:需要 API(8000,已迁移+seed)在跑。
 *   cd apps/api && uv run uvicorn app.main:app --port 8000
 * web(5173)dev server 由本配置自动拉起;
 * 管理端 e2e(SUPERDL_ADMIN_E2E=1,tests/admin.spec.ts)开启时再拉起 admin(5174)。
 */
const adminE2E = Boolean(process.env.SUPERDL_ADMIN_E2E);

export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: "http://localhost:5173",
    locale: "zh-CN", // CI chromium 默认英文环境;钉死避免语言探测翻转致中文定位器失配
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
