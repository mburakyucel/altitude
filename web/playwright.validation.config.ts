import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// Disposable container validation retains Chromium's own sandbox.
export default defineConfig({
  ...base,
  use: { ...base.use, launchOptions: { ...base.use?.launchOptions, chromiumSandbox: true } },
});
