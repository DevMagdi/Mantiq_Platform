import path from "node:path";

import { defineConfig } from "@playwright/test";
import { loadEnv } from "vite";

/**
 * E2E runs against the real stack: the API is started with migrations + demo seed, and Vite proxies
 * /api to it. `E2E_SKIP_WEBSERVER=1` points the suite at services you started yourself (e.g. a CI job
 * that boots docker compose first).
 *
 * Two rules that broke CI once, so they are spelled out here:
 * - `webServer.env` *replaces* the environment, never just adds to it — so `process.env` is merged in,
 *   otherwise the server loses `PATH` and runs a python without our dependencies.
 * - no `bash -lc`: a login shell re-sources profiles and is the only reason the old command found a
 *   python at all. Plain `sh -c` (what Playwright uses) plus a merged env is enough.
 */
const env = { ...loadEnv("", process.cwd(), ""), ...process.env };
const API_PORT = env.E2E_API_PORT ?? "8000";
const WEB_PORT = env.E2E_WEB_PORT ?? "5173";
const skip = env.E2E_SKIP_WEBSERVER === "1";

/**
 * `E2E_USE_PREVIEW=1` serves the built bundle through `vite preview` instead of the dev server — CI
 * uses it so the suite measures the app rather than Vite's first-request transforms. The port is
 * published back into the environment because e2e/global-setup.ts warms the same origin.
 */
const usePreview = env.E2E_USE_PREVIEW === "1";
const webPort = usePreview ? (env.E2E_WEB_PREVIEW_PORT ?? WEB_PORT) : WEB_PORT;
const webCommand = usePreview
  ? `npm run build && npx vite preview --host 0.0.0.0 --port ${webPort} --strictPort`
  : `npm run dev -- --host 0.0.0.0 --port ${webPort} --strictPort`;
const backendDir = path.resolve(process.cwd(), "../backend");

/** The suite needs its own database. Locally that is SQLite in `backend/`; a CI job may export
 *  `DATABASE_URL` for a real Postgres instead, and we must not overwrite that. */
const apiEnv: Record<string, string> = {
  ...process.env,
  ENV: "dev",
  INTEGRATIONS_DRIVER: "sim",
  CELERY_TASK_ALWAYS_EAGER: "1",
  STORAGE_DIR: path.resolve(process.cwd(), "../.e2e-storage"),
  DATABASE_URL: env.DATABASE_URL ?? `sqlite+aiosqlite:///${path.join(backendDir, "e2e.db")}`,
};
export default defineConfig({
  globalSetup: "./e2e/global-setup.ts",
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  // `json` is not decoration: CI keeps at most 50 annotations per check-run and the raw job log is served
  // from blob storage a reviewer's sandbox cannot read, so failure details were invisible and got guessed at.
  // The file is what `frontend/scripts/ci-e2e-digest.py` turns into a PR comment.
  reporter: process.env.CI
    ? [["github"], ["html", { open: "never" }], ["json", { outputFile: "/tmp/playwright-results.json" }]]
    : [["list"], ["html", { open: "never" }]],
  outputDir: "test-results/e2e",
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    locale: "ar-EG",
    timezoneId: "Africa/Cairo",
    browserName: "chromium",
  },
  projects: [
    // the screenshot sizes the spec asks for: 1440×900 desktop and 390×844 phone
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true } },
  ],
  webServer: skip
    ? undefined
    : [
        {
          // `tee` is not decoration: when the suite fails because requests *stop being answered*, the only
        // witness is the API's own access log — and CI's job log is served from blob storage that a reviewing
        // sandbox cannot read. `scripts/ci-e2e-digest.py` posts the tail of this file on the PR (see run
        // 40572114764: 19 mobile failures said "login failed", and nothing else said why).
        command: `rm -f e2e.db && python -m alembic -c alembic.ini upgrade head && python -m app.seed && python -m uvicorn app.main:app --host 0.0.0.0 --port ${API_PORT} 2>&1 | tee /tmp/e2e-api.log`,
          cwd: backendDir,
          url: `http://127.0.0.1:${API_PORT}/healthz`,
          reuseExistingServer: !process.env.CI,
          timeout: 180_000,
          stdout: "pipe",
          stderr: "pipe",
          env: apiEnv,
        },
        {
          command: webCommand,
          cwd: process.cwd(),
          url: `http://127.0.0.1:${webPort}`,
          reuseExistingServer: !process.env.CI,
          timeout: 180_000,
          stdout: "pipe",
          stderr: "pipe",
          env: {
            ...process.env,
            VITE_API_ORIGIN: `http://127.0.0.1:${API_PORT}`,
            VITE_PREVIEW_PORT: webPort,
            E2E_WEB_PORT: webPort,
            E2E_WEB_PREVIEW_PORT: webPort,
            E2E_USE_PREVIEW: usePreview ? "1" : "0",
          },
        },
      ],
});
