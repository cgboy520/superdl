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
    preservePatterns: [
      "app.title",
      "instances.series*",
      "help.faq*",
      "help.category.*",
      "support.selfHelp.*",
      "billing.refundOrder*",
      "nav.group*",
    ],
    ignoreNamespaces: ["shared", "errors"],
    primaryLanguage: "zh-CN",
  },
  types: {
    input: ["src/locales/zh-CN/*.json", "../../packages/ui/locales/zh-CN/*.json"],
    output: "src/types/i18next.d.ts",
  },
  lint: {
    checkInterpolationParams: true,
    checkConcatenation: "warn",
  },
});
