import { resolve } from "node:path";
import { availableParallelism } from "node:os";
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // Separate from Vitest's *.test/spec.ts discovery without changing the unit suite.
  testMatch: "**/*.pw.ts",
  outputDir: "./ui-artifacts/results",
  reporter: [["list"], ["html", { outputFolder: "ui-artifacts/report", open: "never" }]],
  workers: process.env.CI ? Math.max(2, Math.floor(availableParallelism() / 2)) : 2,
  fullyParallel: Boolean(process.env.CI),
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  use: {
    // fixtures.ts supplies each test's disposable loopback service. No live-service fallback.
    browserName: "chromium",
    // I-20260907-041446: the host's installed Chrome profile denies sandboxed networking.
    // pnpm ui sets a shared, writable browser cache under the Altitude home before runner startup.
    channel: "chromium",
    launchOptions: {
      chromiumSandbox: false,
      // Crashpad also needs a writable directory even with Playwright's temporary browser profile.
      env: { ...process.env, XDG_CONFIG_HOME: resolve("ui-artifacts/browser-config") },
    },
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "phone",
      use: {
        ...devices["Pixel 7"],
        defaultBrowserType: "chromium",
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 1,
      },
    },
    { name: "desktop", grepInvert: /@phone-only/, use: { viewport: { width: 1440, height: 900 } } },
  ],
});
