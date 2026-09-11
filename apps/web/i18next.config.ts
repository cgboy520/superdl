import { defineConfig } from "i18next-cli";

export default defineConfig({
  locales: ["zh-CN", "en-US"],
  extract: {
    input: ["src/**/*.{ts,tsx}"],
    ignore: ["src/routeTree.gen.ts", "src/**/*.test.*", "src/locales/**", "src/types/**"],
    output: "src/locales/{{language}}/{{namespace}}.json",
    defaultNS: "web",
    functions: ["t", "i18n.t"],
    transComponents: ["Trans"],
    sort: true,
    indentation: 2,
    removeUnusedKeys: true,
    // 动态取键的组显式保护:映射表 t(meta.nameKey);app.title(packages/ui useAppLocale);billing.refundOrder*(REFUND_REASON_CODE 映射)
    preservePatterns: [
      "app.title",
      "instances.series*",
      "help.faq*",
      "support.selfHelp.*",
      "billing.refundOrder*",
      "nav.group*",
    ],
    ignoreNamespaces: ["shared", "errors"], // shared/errors ns 属 packages/ui
    primaryLanguage: "zh-CN",
  },
  types: {
    input: ["src/locales/zh-CN/*.json", "../../packages/ui/locales/zh-CN/*.json"],
    output: "src/types/i18next.d.ts",
  },
  lint: {
    checkInterpolationParams: true,
    checkConcatenation: "warn", // 复用句并排属有意组合
  },
});
