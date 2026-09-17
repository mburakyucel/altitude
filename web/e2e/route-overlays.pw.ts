import { expect } from "@playwright/test";
import { test } from "./fixtures";

// A walkthrough overlay routinely expires as the app sends its next request (voice Send with image
// after the gated transcription). Unguarded, Chromium strands about one in ten.
test("a request sent as a one-shot overlay expires still reaches the service", async ({ page }) => {
  await page.goto("/");
  for (let attempt = 0; attempt < 80; attempt++) {
    await page.route("**/e2e/overlay", (route) => route.fulfill({ json: {} }), { times: 1 });
    const status = await page.evaluate(async () => {
      await fetch("/e2e/overlay");
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 2_000);
      try { return (await fetch("/api/files", { method: "POST", body: "{}", signal: controller.signal })).status; }
      catch { return 0; } finally { clearTimeout(timer); }
    });
    expect(status, `request ${attempt + 1} must not be stranded as its overlay expires`).toBe(405);
  }
});
