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
    primaryLanguage: "zh-CN",
  },
  types: {
    input: ["src/locales/zh-CN/*.json"],
    output: "src/types/i18next.d.ts",
  },
  lint: {
    checkInterpolationParams: true,
    checkConcatenation: "error",
  },
});
