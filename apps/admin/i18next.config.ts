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
    // shared/errors 属 packages/ui;menu/roles 等经映射表动态取键,显式保护;
    // errorPage/notFound.title/common.retry 由 packages/ui ErrorPages 引用,扫不到用点;
    // overview.severity* 经 SEVERITY_LABEL_KEY 映射表动态取键(lib/alertLink.ts)
    ignoreNamespaces: ["shared", "errors"],
    preservePatterns: ["menu.*", "roles.*", "nodes.phase*", "nodes.pool*", "nodes.heatLegend*", "cluster.comp*", "cluster.cfg*", "finance.anomaly*", "platform.source*", "platform.nav*", "platform.tab*", "platform.groupIntro*", "platform.fieldExtra*", "errorPage.*", "notFound.*", "overview.severity*"],
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
