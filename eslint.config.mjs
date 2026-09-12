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
      "**/resources.d.ts",
      "packages/api-client/src/generated/**",
      "apps/api/**",
      "deploy/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      // 既有约定:事件回调里 `() => void promise` 显式丢弃(与 no-floating-promises 配套),不强制改块体
      "@typescript-eslint/no-confusing-void-expression": ["error", { ignoreArrowShorthand: true }],
      // 两处刻意抛非 Error:TanStack Router 的 throw redirect() 与 mutator 的结构化 ApiError(isApiError 守卫)
      "@typescript-eslint/only-throw-error": ["error", { allow: ["Redirect", "ApiError"] }],
      // 数字插值安全且全仓惯用(¥${int}.${frac} / v${no} / ${count} 条)
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
      // 与 no-non-null-assertion 冲突:本仓禁止 `!`,该规则会把 `as` 收成 `!`,关闭
      "@typescript-eslint/non-nullable-type-assertion-style": "off",
      // `x || undefined` / `x || "—"` 对原始类型是刻意的 falsy 归一(空串/0/false 即缺省);对象侧仍强制 ??
      "@typescript-eslint/prefer-nullish-coalescing": [
        "error",
        { ignorePrimitives: { string: true, number: true, boolean: true } },
      ],
      "no-restricted-globals": [
        "error",
        { name: "fetch", message: "使用 @superdl/api-client 生成的 hooks,禁止手写 fetch" },
      ],
      "no-restricted-syntax": [
        "error",
        {
          // a11y:纯动作用 <Button type="link">,不用无 href 的链接
          selector:
            "JSXOpeningElement[name.type='JSXMemberExpression'][name.object.name='Typography'][name.property.name='Link']:not(:has(> JSXAttribute[name.name='href']))",
          message: 'Typography.Link 必须带 href;纯动作请用 <Button type="link" size="small">',
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
  {
    // 测试与浏览器 API mock:空函数/空方法即占位语义
    files: ["**/*.test.{ts,tsx}", "**/test/**"],
    rules: {
      "@typescript-eslint/no-empty-function": "off",
    },
  },
);
