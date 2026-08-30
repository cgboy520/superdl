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
    // 经映射表动态取键(t(meta.nameKey))的组,extract 识别不到用点,显式保护;
    // app.title 由共享层 packages/ui useAppLocale 引用,同样扫不到用点;
    // billing.refundOrder* 经 REFUND_REASON_CODE 映射表动态取键(_console.billing.tsx)
    preservePatterns: [
      "app.title",
      "instances.series*",
      "help.faq*",
      "support.selfHelp.*",
      "billing.refundOrder*",
    ],
    ignoreNamespaces: ["shared", "errors"], // shared ns 属 packages/ui,由其 locales.test 守护,不归本 app extract 管
    primaryLanguage: "zh-CN",
  },
  types: {
    input: ["src/locales/zh-CN/*.json", "../../packages/ui/locales/zh-CN/*.json"],
    output: "src/types/i18next.d.ts",
  },
  lint: {
    checkInterpolationParams: true,
    checkConcatenation: "warn", // 复用句(dailyCostNote 等)与另一句以分号并排属有意组合
  },
});
