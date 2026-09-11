// 全仓统一 ESLint 配置(flat config)
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "**/dist/**",
      "**/node_modules/**",
      "**/routeTree.gen.ts",
      "packages/api-client/src/generated/**",
      "apps/api/**",
      "deploy/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser },
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "no-restricted-globals": ["error", { name: "fetch", message: "使用 @superdl/api-client 生成的 hooks,禁止手写 fetch" }],
      "no-restricted-syntax": [
        "error",
        {
          // a11y:纯动作用 <Button type="link">,不用无 href 的链接
          selector:
            "JSXOpeningElement[name.type='JSXMemberExpression'][name.object.name='Typography'][name.property.name='Link']:not(:has(> JSXAttribute[name.name='href']))",
          message: "Typography.Link 必须带 href;纯动作请用 <Button type=\"link\" size=\"small\">",
        },
      ],
    },
  },
  {
    // apps/*/src 数据访问只走 @superdl/api-client 生成 fetcher,禁 customFetch 与直接 import mutator
    files: ["apps/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@superdl/api-client",
              importNames: ["customFetch"],
              message: "禁止直接用 customFetch 手写 URL,使用 @superdl/api-client 生成的 fetcher/hooks",
            },
          ],
          patterns: [
            {
              group: ["**/mutator", "**/mutator.ts", "**/api-client/src/*", "**/api-client/src/**"],
              message: "禁止绕过包入口直接 import mutator(customFetch),使用 @superdl/api-client 生成的 fetcher/hooks",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["packages/api-client/src/mutator.ts"],
    rules: {
      "no-restricted-globals": "off",
    },
  },
);
