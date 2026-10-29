import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    css: false,
    // `load/**` is the R8 Playwright load spec — it needs a real browser and 25 contexts, not jsdom
    exclude: ["e2e/**", "load/**", "node_modules/**", "dist/**"],
    coverage: { provider: "v8", reporter: ["text", "html"], include: ["src/**"] },
  },
});
