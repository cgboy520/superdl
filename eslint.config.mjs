// 全仓统一 ESLint 配置(flat config)
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

/** 两端共用的 a11y 语法约束 */
const A11Y_SYNTAX = [
  {
    // a11y:纯动作用 <Button type="link">,不用无 href 的链接
    selector:
      "JSXOpeningElement[name.type='JSXMemberExpression'][name.object.name='Typography'][name.property.name='Link']:not(:has(> JSXAttribute[name.name='href']))",
    message: 'Typography.Link 必须带 href;纯动作请用 <Button type="link" size="small">',
  },
  {
    // ui-ux-spec §1 规则 4:antd 6 原生 disabled 按钮不可聚焦、无鼠标事件,Tooltip 弹不出原因;条件禁用一律 GatedButton
    selector:
      "JSXElement[openingElement.name.name='Tooltip'] JSXOpeningElement[name.name='Button'] > JSXAttribute[name.name='disabled']",
    message: "Tooltip 直接包 disabled Button 弹不出原因;改用 @superdl/ui/components 的 <GatedButton reason={…}>",
  },
];

/** 设计 token 单一事实源(ui-ux-spec §2):颜色 / 间距 / 抽屉宽度不许写字面量 */
const TOKEN_SYNTAX = [
  {
    selector: "Literal[value=/^#[0-9a-fA-F]{3,8}$/]",
    message:
      "颜色字面量禁止进业务代码:静态色取 packages/ui tokens(brand / statusColors / skuTierMap 等),随主题的语义色用 useThemeColors()",
  },
  {
    selector:
      "JSXOpeningElement[name.name='Space'] > JSXAttribute[name.name='size'] > JSXExpressionContainer > Literal[value>0]",
    message: "Space 间距只用 space token(xs 4 / sm 8 / md 12 / lg 16 / xl 24 / xxl 32);无间距写 size={0}",
  },
  {
    selector:
      "JSXOpeningElement[name.object.name='Space'] > JSXAttribute[name.name='size'] > JSXExpressionContainer > Literal[value>0]",
    message: "Space 间距只用 space token(xs 4 / sm 8 / md 12 / lg 16 / xl 24 / xxl 32);无间距写 size={0}",
  },
  {
    selector: "JSXOpeningElement[name.name='Drawer'] > JSXAttribute[name.name='width']",
    message: "Drawer 宽度走 size={drawerWidth.md|lg}(窄屏自动收到 100vw),不用 width",
  },
  {
    selector:
      "JSXOpeningElement[name.name='Drawer'] > JSXAttribute[name.name='size'] > JSXExpressionContainer > Literal[value>=0]",
    message: "Drawer 宽度只取 drawerWidth.md / drawerWidth.lg 或 layout.navDrawerWidth",
  },
  {
    selector: "JSXOpeningElement[name.name='Drawer'] > JSXAttribute[name.name='size'][value.value=/px/]",
    message: "Drawer 宽度只取 drawerWidth.md / drawerWidth.lg 或 layout.navDrawerWidth",
  },
];

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
      // TanStack Query 无参变更的惯用类型变量:useMutation<void, ...>(仅放行泛型实参位)
      "@typescript-eslint/no-invalid-void-type": ["error", { allowInGenericTypeArguments: true }],
      "no-restricted-globals": [
        "error",
        { name: "fetch", message: "使用 @superdl/api-client 生成的 hooks,禁止手写 fetch" },
      ],
      "no-restricted-syntax": ["error", ...A11Y_SYNTAX],
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
    // 设计 token 是单一事实源:业务代码不写颜色 / 间距 / 抽屉宽度字面量。token 定义与对比度回归自身豁免。
    files: ["apps/**/*.{ts,tsx}", "packages/ui/src/**/*.{ts,tsx}"],
    ignores: [
      "packages/ui/src/tokens.ts",
      "packages/ui/src/color.ts",
      "packages/ui/src/status.ts",
      "**/*.test.{ts,tsx}",
    ],
    rules: {
      "no-restricted-syntax": ["error", ...A11Y_SYNTAX, ...TOKEN_SYNTAX],
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
