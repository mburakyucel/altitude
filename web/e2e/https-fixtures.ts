import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test as base } from "./fixtures";

/** Real HTTPS acceptance trusts only a disposable CA in Chromium's private NSS home. It uses
 * the runner's shared launch options unchanged, including its Chromium sandbox policy. */
export const test = base.extend({
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
