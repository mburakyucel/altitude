import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { test as base } from "./e2e/fixtures";
import { resolve, join } from "node:path";
import { availableParallelism, tmpdir } from "node:os";
import { defineConfig, devices, expect } from "@playwright/test";
import { captures } from "./capture-reporter";

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
    ["./browser-evidence.ts"], ...(captures ? [["./capture-reporter.ts"] as const] : [])],
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
      // Capture stays inside Chromium; walkthroughs never request a physical microphone. In a macOS validation run
      // the GPU compositor has no display to pace frames by and stops animation frames after the first one;
      // software compositing paces them by timer, as on Linux (#617).
      args: ["--enable-automation", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
        ...(process.platform === "darwin" ? ["--disable-gpu"] : [])],
      // Crashpad also needs a writable directory even with Playwright's temporary browser profile.
      // On Linux the host's ALSA configuration loads its sound server plugin into Chromium's audio service,
      // which intermittently crashed it and ended the fake microphone mid-walkthrough; without it,
      // Chromium plays to its fake output device, as on hosted runners.
      env: { ...process.env, XDG_CONFIG_HOME: process.env.ALTITUDE_UI_BROWSER_CONFIG ?? resolve("ui-artifacts/browser-config"),
        ALSA_CONFIG_PATH: "/dev/null" },
    },
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: captures ? "on" : "off",
  },
  projects: [
    {
      name: "phone",
      grepInvert: /@desktop-only/,
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

/** Real HTTPS acceptance trusts only a disposable CA in Chromium's private NSS home. It uses
 * the runner's shared launch options unchanged, including its Chromium sandbox policy. */
export const httpsTest = base.extend({
  context: async ({ context, altitude, playwright, launchOptions, channel, headless }, use, info) => {
    const home = mkdtempSync(join(tmpdir(), "install-pair-browser-"));
    const certificate = join(home, "ca.crt");
    const db = join(home, ".pki/nssdb");
    const certutil = process.env.ALTITUDE_TEST_CERTUTIL || "certutil";
    let browser;
    try {
      mkdirSync(db, { recursive: true });
      // The service's public certificate is fixture data, not a system/host trust-store mutation.
      writeFileSync(certificate, readFileSync(altitude.certificate!, "utf8"));
      const untrusted = await context.newPage();
      try {
        let refused = false;
        try { await untrusted.goto(altitude.url); }
        catch (error) { refused = String(error).includes("ERR_CERT_AUTHORITY_INVALID"); }
        expect(refused, "The fictional CA must be trusted explicitly").toBe(true);
      } finally { await untrusted.close(); }
      execFileSync(certutil, ["-N", "--empty-password", "-d", "sql:" + db]);
      execFileSync(certutil, ["-A", "-d", "sql:" + db, "-n", "Fictional Altitude", "-t", "C,,", "-i", certificate]);
      browser = await playwright.chromium.launch({ ...launchOptions, channel, headless,
        env: { ...launchOptions.env, HOME: home, XDG_CONFIG_HOME: join(home, "config"), XDG_CACHE_HOME: join(home, "cache") } });
      const session = await browser.newBrowserCDPSession();
      try {
        const command = await session.send("Browser.getBrowserCommandLine");
        await info.attach("trusted-https-browser", { body: JSON.stringify({ version: browser.version(),
          command: command.arguments, configurationSandbox: launchOptions.chromiumSandbox,
          untrustedCaRejected: true, verification: "enabled", trustHome: home }), contentType: "application/json" });
      } finally { await session.detach(); }
      const view = info.project.use;
      const trusted = await browser.newContext({ ignoreHTTPSErrors: false, viewport: view.viewport, userAgent: view.userAgent,
        isMobile: view.isMobile, hasTouch: view.hasTouch, deviceScaleFactor: view.deviceScaleFactor });
      await trusted.route(/^https?:\/\/(?!127\.0\.0\.1[:/])/, route => route.abort());
      try { await use(trusted); } finally { await trusted.close(); }
    } finally {
      await browser?.close();
      rmSync(home, { recursive: true, force: true });
      await info.attach("trusted-https-cleanup", { body: JSON.stringify({ browserClosed: true, homeRemoved: true }), contentType: "application/json" });
    }
  },
});
