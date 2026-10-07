import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

test("full Chromium opens blank and fictional content", async ({ browser, page }, info) => {
  test.setTimeout(30_000);
  await page.goto("about:blank");
  await page.setContent("<!doctype html><title>Fictional preflight</title><h1>Fictional project</h1>");
  await expect(page.getByRole("heading", { name: "Fictional project" })).toBeVisible();
  const session = await browser.newBrowserCDPSession();
  const { arguments: command } = await session.send("Browser.getBrowserCommandLine");
  await session.detach();
  await info.attach("preflight", { body: JSON.stringify({ version: browser.version(), title: await page.title(),
    command, executableSha256: createHash("sha256").update(readFileSync(command[0])).digest("hex") }),
    contentType: "application/json" });
});
