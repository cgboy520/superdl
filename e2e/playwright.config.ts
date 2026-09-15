import { defineConfig } from "@playwright/test";

/** 需已迁移并初始化的 API(8000)及 worker;配置启动 web,设置非空 SUPERDL_ADMIN_E2E 时也启动 admin。 */
const adminE2E = Boolean(process.env.SUPERDL_ADMIN_E2E);

export default defineConfig({
  testDir: "./tests",
  timeout: 300_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: "http://localhost:5173",
    locale: "zh-CN",
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
