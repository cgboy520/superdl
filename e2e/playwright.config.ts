import { defineConfig } from "@playwright/test";

/** Needs a migrated, seeded API (8000) and a running worker; the config starts web, and admin too when SUPERDL_ADMIN_E2E is non-empty. */
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
