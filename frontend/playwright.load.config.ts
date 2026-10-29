import path from "node:path";

import { defineConfig } from "@playwright/test";
import { loadEnv } from "vite";

/**
 * R8 load config — deliberately separate from `playwright.config.ts`.
 *
 * The load spec runs for as long as the scenario says (default 10 minutes), so it must never be picked up by
 * the regular e2e run: `testDir` points at `./load`, and the suite is a single test that manages its own
 * 25 browser contexts.
 *
 * The stack is started here exactly the way `playwright.config.ts` starts it for e2e — same migrations, same
 * seed, same `vite preview` — because those command strings are the ones CI has already proven. The one
 * addition is `scripts/load/seed_volume.py`, without which the screens the load users open would be empty
 * (R8 §4 asks for ≥ 200 leads, ≥ 100 open requests, ≥ 50 clients). Set `E2E_SKIP_WEBSERVER=1` to point the
 * suite at a stack you started yourself (`LOAD_WEB_URL`).
 *
 * Same two rules as the main config, kept here so nobody re-learns them the hard way:
 * - `webServer.env` *replaces* the environment → merge `process.env` back in, or the server loses `PATH`;
 * - no `bash -lc`.
 */
const env = { ...loadEnv("", process.cwd(), ""), ...process.env };
const API_PORT = env.E2E_API_PORT ?? "8000";
const WEB_PORT = env.E2E_WEB_PORT ?? "5173";
const skip = env.E2E_SKIP_WEBSERVER === "1";
const backendDir = path.resolve(process.cwd(), "../backend");
const minutes = env.LOAD_MINUTES ?? "10";

// the spec reads the same origin it asserts against; set before `defineConfig` runs so the test sees it
process.env.LOAD_WEB_URL = process.env.LOAD_WEB_URL ?? `http://127.0.0.1:${WEB_PORT}`;
process.env.LOAD_MINUTES = minutes;

const apiEnv: Record<string, string> = {
  ...process.env,
  ENV: "dev",
  INTEGRATIONS_DRIVER: env.INTEGRATIONS_DRIVER ?? "sim",
  CELERY_TASK_ALWAYS_EAGER: "1",
  STORAGE_DIR: path.resolve(process.cwd(), "../.e2e-storage"),
  DATABASE_URL: env.DATABASE_URL ?? `sqlite+aiosqlite:///${path.join(backendDir, "e2e.db")}`,
};

export default defineConfig({
  testDir: "./load",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 20 * 60_000,
  expect: { timeout: 30_000 },
  reporter: process.env.CI ? [["list"], ["json", { outputFile: "/tmp/r8-load-results.json" }]] : [["list"]],
  outputDir: "test-results/load",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    browserName: "chromium",
    viewport: { width: 1440, height: 900 },
    locale: "ar-EG",
    timezoneId: "Africa/Cairo",
    video: "off",
    trace: "off",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "load" }],
  webServer: skip
    ? undefined
    : [
        {
          // migrations + product seed + the volume the load scenarios assume, then the API itself.
          // `tee` is not decoration: if requests stop being answered, the API's own log is the only witness.
          command: `rm -f e2e.db && python -m alembic -c alembic.ini upgrade head && python -m app.seed && python scripts/load/seed_volume.py --leads 240 --clients 52 --requests 120 && python -m uvicorn app.main:app --host 0.0.0.0 --port ${API_PORT} 2>&1 | tee /tmp/load-api.log`,
          cwd: backendDir,
          url: `http://127.0.0.1:${API_PORT}/healthz`,
          reuseExistingServer: !process.env.CI,
          timeout: 240_000,
          stdout: "pipe",
          stderr: "pipe",
          env: apiEnv,
        },
        {
          // the built bundle, exactly like the e2e job: `vite preview` so the numbers are the app's own
          command: `npm run build && npx vite preview --host 0.0.0.0 --port ${WEB_PORT} --strictPort`,
          cwd: process.cwd(),
          url: `http://127.0.0.1:${WEB_PORT}/`,
          reuseExistingServer: !process.env.CI,
          timeout: 300_000,
          stdout: "pipe",
          stderr: "pipe",
          env: {
            ...process.env,
            VITE_API_ORIGIN: `http://127.0.0.1:${API_PORT}`,
            VITE_PREVIEW_PORT: WEB_PORT,
            E2E_WEB_PORT: WEB_PORT,
            E2E_WEB_PREVIEW_PORT: WEB_PORT,
            E2E_USE_PREVIEW: "1",
          },
        },
      ],
});
