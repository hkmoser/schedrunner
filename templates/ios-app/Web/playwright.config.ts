import { defineConfig, devices } from "@playwright/test";

// Real-browser e2e for the PWA. Runs the Vite dev server (which falls back to the
// public/*-sample.json fixtures when no backend is present), emulating an iPhone —
// the viewport where the layout/scroll regressions actually surface. The iPhone 13
// profile runs on WebKit (the iOS Safari engine), matching the real install target.
// Runs in CI (GitHub Actions has open network to install WebKit); locally with
// `npm run test:e2e` once `npx playwright install webkit` has run.
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://localhost:4173",
    trace: "on-first-retry",
  },
  projects: [
    { name: "iphone", use: { ...devices["iPhone 13"] } },
  ],
  webServer: {
    command: "npm run dev -- --port 4173 --strictPort",
    url: "http://localhost:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
