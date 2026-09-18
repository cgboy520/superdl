import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

const A11Y_SYNTAX = [
  {
    selector:
      "JSXOpeningElement[name.type='JSXMemberExpression'][name.object.name='Typography'][name.property.name='Link']:not(:has(> JSXAttribute[name.name='href']))",
    message: 'Typography.Link needs an href; for a pure action use <Button type="link" size="small">',
  },
  {
    selector:
      "JSXElement[openingElement.name.name='Tooltip'] JSXOpeningElement[name.name='Button'] > JSXAttribute[name.name='disabled']",
    message:
      "A Tooltip around a disabled Button never shows the reason; use <GatedButton reason={…}> from @superdl/ui/components",
  },
];

const TOKEN_SYNTAX = [
  {
    selector: "Literal[value=/^#[0-9a-fA-F]{3,8}$/]",
    message:
      "No colour literals in feature code: static colours come from the packages/ui tokens (brand / statusColors / skuTierMap …), theme-dependent semantic colours from useThemeColors()",
  },
  {
    selector:
      "JSXOpeningElement[name.name='Space'] > JSXAttribute[name.name='size'] > JSXExpressionContainer > Literal[value>0]",
    message:
      "Space gaps use the space tokens only (xs 4 / sm 8 / md 12 / lg 16 / xl 24 / xxl 32); write size={0} for no gap",
  },
  {
    selector:
      "JSXOpeningElement[name.object.name='Space'] > JSXAttribute[name.name='size'] > JSXExpressionContainer > Literal[value>0]",
    message:
      "Space gaps use the space tokens only (xs 4 / sm 8 / md 12 / lg 16 / xl 24 / xxl 32); write size={0} for no gap",
  },
  {
    selector: "JSXOpeningElement[name.name='Drawer'] > JSXAttribute[name.name='width']",
    message:
      "Drawer width goes through size={drawerWidth.md|lg} (narrow screens collapse to 100vw automatically); do not use width",
  },
  {
    selector:
      "JSXOpeningElement[name.name='Drawer'] > JSXAttribute[name.name='size'] > JSXExpressionContainer > Literal[value>=0]",
    message: "Drawer size takes only drawerWidth.md / drawerWidth.lg or layout.navDrawerWidth",
  },
  {
    selector: "JSXOpeningElement[name.name='Drawer'] > JSXAttribute[name.name='size'][value.value=/px/]",
    message: "Drawer size takes only drawerWidth.md / drawerWidth.lg or layout.navDrawerWidth",
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
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", ignoreRestSiblings: true }],
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
        { name: "fetch", message: "Use the hooks generated in @superdl/api-client; no hand-written fetch" },
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
              message:
                "Do not hand-write URLs with customFetch; use the fetchers / hooks generated in @superdl/api-client",
            },
          ],
          patterns: [
            {
              group: ["**/mutator", "**/mutator.ts", "**/api-client/src/*", "**/api-client/src/**"],
              message:
                "Do not import mutator (customFetch) around the package entry; use the fetchers / hooks generated in @superdl/api-client",
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
