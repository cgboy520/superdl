import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

const A11Y_SYNTAX = [
  {
    selector:
      "JSXOpeningElement[name.type='JSXMemberExpression'][name.object.name='Typography'][name.property.name='Link']:not(:has(> JSXAttribute[name.name='href']))",
    message: 'Typography.Link 必须带 href;纯动作请用 <Button type="link" size="small">',
  },
  {
    selector:
      "JSXElement[openingElement.name.name='Tooltip'] JSXOpeningElement[name.name='Button'] > JSXAttribute[name.name='disabled']",
    message: "Tooltip 直接包 disabled Button 弹不出原因;改用 @superdl/ui/components 的 <GatedButton reason={…}>",
  },
];

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
      "@typescript-eslint/no-confusing-void-expression": ["error", { ignoreArrowShorthand: true }],
      "@typescript-eslint/only-throw-error": ["error", { allow: ["Redirect", "ApiError"] }],
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
      "@typescript-eslint/non-nullable-type-assertion-style": "off",
      "@typescript-eslint/prefer-nullish-coalescing": [
        "error",
        { ignorePrimitives: { string: true, number: true, boolean: true } },
      ],
      "@typescript-eslint/no-invalid-void-type": ["error", { allowInGenericTypeArguments: true }],
      "no-restricted-globals": [
        "error",
        { name: "fetch", message: "使用 @superdl/api-client 生成的 hooks,禁止手写 fetch" },
      ],
      "no-restricted-syntax": ["error", ...A11Y_SYNTAX],
    },
  },
  {
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
    files: ["**/*.test.{ts,tsx}", "**/test/**"],
    rules: {
      "@typescript-eslint/no-empty-function": "off",
    },
  },
);
