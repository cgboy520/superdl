import { defineConfig } from "i18next-cli";

export default defineConfig({
  locales: ["zh-CN", "en-US"],
  extract: {
    input: ["src/**/*.{ts,tsx}"],
    ignore: ["src/routeTree.gen.ts", "src/**/*.test.*", "src/locales/**", "src/types/**"],
    output: "src/locales/{{language}}/{{namespace}}.json",
    defaultNS: "admin",
    functions: ["t", "i18n.t"],
    transComponents: ["Trans"],
    sort: true,
    indentation: 2,
    removeUnusedKeys: true,
    // 映射表动态取键的命名空间显式保护(menu/roles、overview.severity* 等)
    ignoreNamespaces: ["shared", "errors"],
    preservePatterns: [
      "menu.*",
      "roles.*",
      "nodes.phase*",
      "nodes.pool*",
      "nodes.heatLegend*",
      "cluster.comp*",
      // 体检面板按 key 动态取:事实 label / 对象表列名 / 判据 / 影响面
      "cluster.fact.*",
      "cluster.objcol.*",
      "cluster.criterion.*",
      "cluster.impact.*",
      "cluster.cfg*",
      "finance.anomaly*",
      "platform.source*",
      "platform.nav*",
      "platform.tab*",
      "platform.groupIntro*",
      "platform.fieldExtra*",
      "platform.field.*",
      "platform.provider.*",
      "platform.riskOff.*",
      "settings.policy.*",
      "overview.severity*",
    ],
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
