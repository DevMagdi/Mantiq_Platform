import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api and /media to FastAPI so the SPA only ever uses relative URLs.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  const target = env.VITE_API_ORIGIN ?? "http://127.0.0.1:8000";
  const proxy = {
    "/api": { target, changeOrigin: true },
    "/media": { target, changeOrigin: true },
  };
  return {
    plugins: [react()],
    resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
    // `preview` gets the same proxy as `server`: the e2e suite can run against the built bundle (no
    // cold-start transforms) while the SPA keeps using relative /api and /media URLs in both modes.
    server: { host: "0.0.0.0", port: Number(env.VITE_PORT ?? 5173), strictPort: false, allowedHosts: true, proxy },
    preview: { host: "0.0.0.0", port: Number(env.VITE_PREVIEW_PORT ?? 4173), allowedHosts: true, proxy },
    build: { sourcemap: true, target: "es2022", chunkSizeWarningLimit: 700 },
  };
});
