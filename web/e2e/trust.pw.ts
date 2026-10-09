import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/* Chromium against Altitude over real HTTPS from a CA it does not trust (SPEC.md §3.16). Proceeding past the
 * certificate warning lets the served certificate through and nothing else, so the trust check's second
 * certificate is refused and the code field stays hidden. Ignoring certificate errors goes the same way: Chromium refuses
 * each untrusted certificate with an alert before it ignores the error and connects again. A browser that trusts the
 * CA is walked with the service's replies in pairing.pw.ts and over real TLS in tests/test_https_server.py. */
test.use({ paired: false, serviceScript: "trust-service.py" });

function screen(page: Page) {
  const trust = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: /^Trust Altitude’s certificate/ }) });
  return {
    heading: page.getByRole("heading", { name: "Pair this device" }),
    trust,
    notTrusted: trust.getByText("Not trusted yet", { exact: true }),
    trusted: trust.getByText("Trusted", { exact: true }),
    field: page.getByLabel("Pairing code"),
  };
}

test("a browser that proceeded past the certificate warning is not trusted and is not offered the code", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const view = screen(page);
  await page.goto("/").catch(() => undefined);  // Chromium refuses the unknown CA with its warning page
  await walk.state("certificate-warning", {
    visible: [page.locator("#details-button")],
    hidden: [view.heading],
  });
  await walk.state("proceeded-not-trusted", {
    action: async () => {
      await page.locator("#details-button").click();
      await page.locator("#proceed-link").click();
    },
    visible: [view.heading, view.notTrusted, view.trust.getByRole("button", { name: "Check again" })],
    hidden: [view.field, view.trusted],
  });
  await walk.state("check-again-still-not-trusted", {
    action: () => view.trust.getByRole("button", { name: "Check again" }).click(),
    visible: [view.notTrusted],
    hidden: [view.field],
  });
});

test.describe("told to ignore certificate errors", () => {
  test.use({ ignoreHTTPSErrors: true });

  test("the browser is still not trusted, because it refuses the second certificate before ignoring that", async ({ page }, info) => {
    const walk = walkthrough(page, info);
    const view = screen(page);
    await walk.open("/");
    await walk.state("ignoring-errors-not-trusted", { visible: [view.heading, view.notTrusted], hidden: [view.trusted, view.field] });
  });
});
