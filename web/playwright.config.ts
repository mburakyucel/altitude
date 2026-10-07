import { resolve } from "node:path";
import { availableParallelism } from "node:os";
import { defineConfig, devices } from "@playwright/test";

// The recovery lane exercises local project/draft/clipboard fixtures; the full suite keeps
// Chromium's full distribution, whose notification APIs are absent from the headless shell.
const shell = process.env.ALTITUDE_UI_HEADLESS_SHELL === "1";
const results = process.env.ALTITUDE_UI_RESULTS;

export default defineConfig({
  testDir: "./e2e",
  // Separate from Vitest's *.test/spec.ts discovery without changing the unit suite.
  testMatch: "**/*.pw.ts",
  outputDir: results ? resolve(results, "tests") : shell ? "./ui-artifacts/shell-results" : "./ui-artifacts/results",
  reporter: [["list"], ["html", { outputFolder: results ? resolve(results, "report") : shell ? "ui-artifacts/report/shell" : "ui-artifacts/report", open: "never" }],
    ["./browser-evidence.ts"]],
  workers: process.env.CI ? availableParallelism() : 2,
  fullyParallel: Boolean(process.env.CI),
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  use: {
    // fixtures.ts supplies each test's disposable loopback service. No live-service fallback.
    browserName: "chromium",
    // I-20260907-041446: the host's installed Chrome profile denies sandboxed networking.
    // pnpm ui sets a shared, writable browser cache under the Altitude home before runner startup.
    channel: shell ? undefined : "chromium",
    launchOptions: {
      chromiumSandbox: false,
      timeout: 30_000,
      // Capture stays inside Chromium; walkthroughs never request a physical microphone.
      args: ["--enable-automation", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
      // Crashpad also needs a writable directory even with Playwright's temporary browser profile.
      env: { ...process.env, XDG_CONFIG_HOME: process.env.XDG_CONFIG_HOME ?? resolve("ui-artifacts/browser-config") },
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
