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
  // 重链路用例要等 worker+reconciler(周期 30s)推进,统一给 5min,各 spec 不再单独 setTimeout
  timeout: 300_000,
  // CI 上给一次重试吸收 reconciler 周期抖动;本地保持 0 以便第一时间看到失败
  retries: process.env.CI ? 1 : 0,
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
