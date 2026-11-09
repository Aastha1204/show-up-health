import { defineConfig } from "@playwright/test";

/**
 * Runs against a local stack: Postgres (docker compose up -d db), the API on :8000 and the web app on :3100.
 * Servers that are already running are reused; otherwise they are started. The database is re-seeded once before
 * the run (global-setup) using E2E_PYTHON (default: python) so every run starts from the same demo data.
 * Uses the installed Microsoft Edge (E2E_CHANNEL=chrome for Chrome) so no browser download is needed.
 */
export default defineConfig({
  testDir: "./tests",
  globalSetup: "./global-setup.ts",
  timeout: 60_000,
  expect: { timeout: 8_000 },
  workers: 1,
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3100",
    channel: process.env.E2E_CHANNEL ?? "msedge",
    headless: true,
    viewport: { width: 1360, height: 900 },
    trace: "retain-on-failure",
  },
  webServer: [
    { command: "python run.py", cwd: "../api", url: "http://127.0.0.1:8000/health", reuseExistingServer: true, timeout: 60_000 },
    { command: "npm run dev", cwd: "../web", url: "http://localhost:3100/login", reuseExistingServer: true, timeout: 120_000 },
  ],
});
