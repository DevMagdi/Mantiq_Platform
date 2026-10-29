import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

/**
 * Flat config for ESLint 9. Rules are the repo's own: strict-but-pragmatic, no style churn
 * (Prettier is not used; the design tokens and formatting live in the source itself).
 */
export default tseslint.config(
  {
    ignores: ["dist/**", "coverage/**", "playwright-report/**", "test-results/**", "src/lib/api/schema.d.ts"],
  },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.es2022 },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/consistent-type-imports": ["error", { prefer: "type-imports", fixStyle: "inline-type-imports" }],
      "@typescript-eslint/no-explicit-any": "error",
      "no-console": ["error", { allow: ["warn", "error"] }],
      eqeqeq: ["error", "always", { null: "ignore" }],
      "prefer-const": "error",
      /*
       * `Cannot read properties of undefined (reading 'toLowerCase')` is the most common crash on a screen
       * that renders server data: one field the API did not send, and the tab is blank. `src/lib/text.ts`
       * already solves it (`lower`/`upper`/`asText` are null-safe by construction) — this rule is what keeps
       * the next component from calling the raw method again. Allow-listed below for the helper itself.
       */
      "no-restricted-properties": [
        "error",
        {
          property: "toLowerCase",
          message: "Use lower(value) from @/lib/text — it survives null/undefined and numbers.",
        },
        {
          property: "toUpperCase",
          message: "Use upper(value) from @/lib/text — it survives null/undefined and numbers.",
        },
      ],
    },
  },
  {
    // the null-safe helpers are the one place that is allowed to call the raw methods
    files: ["src/lib/text.ts"],
    rules: { "no-restricted-properties": "off" },
  },
  {
    // Providers, hooks and shared constants are intentionally co-located with components here.
    files: ["src/lib/**/*.{ts,tsx}", "src/components/**/*.{ts,tsx}"],
    rules: { "react-refresh/only-export-components": "off" },
  },
  {
    files: ["tests/**/*.{ts,tsx}", "src/**/*.test.{ts,tsx}", "e2e/**/*.ts"],
    rules: {
      "no-console": "off",
      "@typescript-eslint/no-explicit-any": "off",
      // tests read computed styles and fixtures that are always defined; nothing here is shipped
      "no-restricted-properties": "off",
    },
  },
  {
    files: ["*.config.{js,ts}", "vite.config.ts", "playwright.config.ts"],
    languageOptions: { globals: { ...globals.node } },
  },
);
