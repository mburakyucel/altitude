import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// The suite's service runs without HTTPS, so each state overlays the certificate on the real device list.
const FINGERPRINT = Array.from({ length: 32 }, (_, index) => (index * 7 + 16).toString(16).toUpperCase().padStart(2, "0")).join(":");
const LIMITED = { name: "Altitude local CA", expires: "Sep 25 04:00:00 2036 GMT", sha256: FINGERPRINT,
  scope: "Names under localhost, local, internal, home.arpa and their subdomains; addresses in 127.0.0.0/8, 10.0.0.0/8, 192.168.0.0/16." };
const UNLIMITED = { name: "mkcert studio@example", expires: "Jan 2 00:00:00 2034 GMT", sha256: FINGERPRINT,
  scope: "No limits: this CA can vouch for any website, so whoever holds its key could impersonate any site to a device that trusts it." };

/** Reload, then bring the certificate facts into a phone's view for the screenshot. */
async function reload(page: Page) {
  await page.reload();
  const facts = page.getByRole("region", { name: "Certificate" }).locator("dl, [role=alert]");
  await facts.scrollIntoViewIfNeeded();
}

async function certificate(page: Page, value: object | null) {
  await page.unroute("**/api/devices");
  await page.route("**/api/devices", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), certificate: value } });
  });
}

test("Settings › Devices names the certificate a phone must match and how to offer it", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const card = page.getByRole("region", { name: "Certificate" });
  const list = page.getByRole("list", { name: "Paired devices" });
  await certificate(page, null);
  await walk.open("/settings/devices");
  await walk.state("01-without-https", { visible: [list], hidden: [card] });
  await certificate(page, LIMITED);
  await walk.state("02-generated-ca", {
    action: () => reload(page),
    visible: [card.getByText("Altitude local CA"), card.getByText("alt tls-share", { exact: true }),
      card.getByText(/^10 17 1E 25 2C 33 3A 41/), card.getByText(/^Names under localhost/)],
    hidden: [card.getByRole("alert")],
  });
  await certificate(page, UNLIMITED);
  await walk.state("03-unconstrained-ca", {
    action: () => reload(page),
    visible: [card.getByText("mkcert studio@example"), card.getByText(/^No limits: this CA can vouch for any website/)],
    hidden: [card.getByText("Altitude local CA")],
  });
  await certificate(page, { error: "HTTPS certificate operation failed: unreadable ca.crt." });
  await walk.state("04-unreadable-ca", {
    action: () => reload(page),
    visible: [card.getByRole("alert").getByText(/Could not read the certificate/)],
    hidden: [card.getByText("alt tls-share", { exact: true })],
  });
  await expect(list).toBeVisible();
});
