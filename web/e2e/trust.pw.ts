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
    trusted: trust.getByRole("status", { name: "Trusted", exact: true }),
    field: page.getByLabel("Pairing code"),
  };
}

test("a host browser that proceeded past the certificate warning is not trusted and is not offered the code @chromium", async ({ page, request }, info) => {
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
  const access = await (await request.get("/api/access", { ignoreHTTPSErrors: true })).json();
  expect(access.trust.local).toBe(true);
  expect(access.trust.command).toBe(true);
  // A desktop browser here is offered alt trust, with the comparison in the steps by hand; a phone compares first.
  await expect(info.project.name === "desktop" ? view.trust.getByText("~/.local/bin/alt trust", { exact: true })
    : view.trust.getByText(/If they differ, delete the download and stop/)).toBeVisible();
  await expect(view.trust.getByText(/if a warning remains, don’t pair/).first()).toBeVisible();
  await walk.state("check-again-still-not-trusted", {
    action: () => view.trust.getByRole("button", { name: "Check again" }).click(),
    visible: [view.notTrusted],
    hidden: [view.field],
  });
});

test.describe("told to ignore certificate errors", () => {
  test.use({ ignoreHTTPSErrors: true });

  test("the browser is still not trusted, because it refuses the second certificate before ignoring that @chromium", async ({ page }, info) => {
    const walk = walkthrough(page, info);
    const view = screen(page);
    await walk.open("/");
    await walk.state("ignoring-errors-not-trusted", { visible: [view.heading, view.notTrusted], hidden: [view.trusted, view.field] });
  });
});
