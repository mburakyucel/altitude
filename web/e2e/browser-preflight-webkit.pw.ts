import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

test("desktop WebKit opens blank and fictional content", async ({ browser, page }, info) => {
  test.skip(browser.browserType().name() !== "webkit", "The emulated iPhone lane owns WebKit preflight");
  test.setTimeout(30_000);
  await page.goto("about:blank");
  await page.setContent("<!doctype html><title>Fictional preflight</title><h1>Fictional project</h1>");
  await expect(page.getByRole("heading", { name: "Fictional project" })).toBeVisible();
  const launcher = browser.browserType().executablePath();
  await info.attach("preflight", { body: JSON.stringify({ engine: browser.browserType().name(), version: browser.version(),
    title: await page.title(), launcher, launcherSha256: createHash("sha256").update(readFileSync(launcher)).digest("hex"),
    boundary: "desktop WebKit; not iOS Safari or native certificate trust" }), contentType: "application/json" });
});
