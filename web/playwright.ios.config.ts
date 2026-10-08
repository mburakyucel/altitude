import { defineConfig, devices } from "@playwright/test";
import base from "./playwright.config";
import { captures } from "./capture-reporter";

// Opt-in emulated iPhone lane (`make ui-ios`): the same walkthroughs in Playwright's WebKit with
// iPhone metrics, touch and user agent. It is desktop WebKit evidence, not iOS Safari, Home Screen,
// microphone or certificate-trust evidence. Tests tagged @chromium need a Chromium-only harness
// capability (Notification, clipboard-write permission, CDP or a replaceable
// getUserMedia) and stay with the required phone/desktop projects; docs/DEVELOPMENT.md lists them.
export default defineConfig({
  ...base,
  outputDir: "./ui-artifacts/ios/results",
  reporter: [["list"], ["html", { outputFolder: "ui-artifacts/ios/report", open: "never" }],
    ...(captures ? [["./capture-reporter.ts"] as const] : [])],
  use: { headless: true, screenshot: "only-on-failure", trace: "retain-on-failure", video: base.use?.video },
  projects: [
    {
      // Named "phone" so specs apply their phone layout and @phone-only walkthroughs.
      name: "phone",
      grepInvert: /@chromium/,
      use: {
        ...devices["iPhone 15"],
        browserName: "webkit",
        viewport: { width: 390, height: 844 },
        // WebKit's mock capture device; nothing reaches a physical microphone.
        permissions: ["microphone"],
      },
    },
  ],
});
