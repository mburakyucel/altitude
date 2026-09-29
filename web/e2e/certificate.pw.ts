import { execFileSync } from "node:child_process";
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
    visible: [card.getByText("Altitude local CA"), card.getByRole("button", { name: "Add a phone" }),
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
    hidden: [card.getByRole("button", { name: "Add a phone" })],
  });
  await expect(list).toBeVisible();
});

/** Application code renders what the phone sees: the share link's QR rows and the guided page. */
function python(code: string): string {
  return execFileSync("python3", ["-c", code], { cwd: "..", encoding: "utf8" });
}
const LINK = "http://192.168.1.20:43567/";
const QR: string[] = JSON.parse(python(`import json; from altitude import qr
print(json.dumps(["".join("1" if d else "0" for d in row) for row in qr.matrix(${JSON.stringify(LINK)})]))`));

test("Settings › Devices › Add a phone shows the QR code with its time left, closes and reports refusals", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const card = page.getByRole("region", { name: "Certificate" });
  const add = card.getByRole("button", { name: "Add a phone" });
  const code = card.getByRole("img", { name: `QR code for ${LINK}` });
  await certificate(page, LIMITED);
  await walk.open("/settings/devices");
  await walk.state("05-add-a-phone", { action: () => add.scrollIntoViewIfNeeded(), visible: [add], hidden: [code, card.getByRole("alert")] });
  // The suite's service listens on loopback only, so its real answer is the refusal.
  await walk.state("06-refused", {
    action: () => add.click(),
    visible: [card.getByRole("alert").getByText(/configured for 127\.0\.0\.1, which only this computer can open/), add],
    hidden: [code],
  });
  const closes: unknown[] = [];
  let seconds = 600;
  let release: () => void = () => undefined;
  let gate: Promise<void> | null = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/devices/share", async (route) => {
    if (gate) await gate;
    await route.fulfill({ json: { link: LINK, seconds, name: LIMITED.name, sha256: FINGERPRINT, qr: QR } });
  });
  let closeFails = true;
  await page.route("**/api/devices/share-close", async (route) => {
    if (closeFails) {
      closeFails = false;
      return route.fulfill({ status: 503, json: { error: "Could not reach Altitude." } });
    }
    closes.push(route.request().postDataJSON());
    await route.fulfill({ json: { closed: true } });
  });
  await walk.state("07-opening", {
    action: () => add.click(),
    visible: [card.getByRole("button", { name: "Opening…" })],
    hidden: [card.getByRole("alert"), code],
  });
  await walk.state("08-qr-code", {
    action: async () => { release(); await card.getByRole("button", { name: "Close" }).scrollIntoViewIfNeeded(); },
    visible: [code, card.getByRole("timer").getByText(/^Closes in (10:00|9:5\d)$/), card.getByText(LINK),
      card.getByText(/^Scan it with the phone’s camera/), card.getByText(/^10 17 1E 25 2C 33 3A 41/)],
    hidden: [add, card.getByRole("alert")],
  });
  await walk.state("09-close-failed", {
    action: () => card.getByRole("button", { name: "Close" }).click(),
    visible: [code, card.getByRole("alert").getByText("The link is still open: Could not reach Altitude."),
      card.getByRole("button", { name: "Close" })],
    hidden: [add, card.getByRole("status")],
  });
  await walk.state("10-closed", {
    action: () => card.getByRole("button", { name: "Close" }).click(),
    visible: [card.getByRole("status").getByText("The link is closed."), add],
    hidden: [code, card.getByRole("timer"), card.getByRole("alert")],
  });
  expect(closes).toEqual([{ link: LINK }]);
  seconds = 2;
  gate = null;
  await add.click();
  await expect(code).toBeVisible();
  await walk.state("11-expired", {
    visible: [card.getByRole("status").getByText("The link is closed."), add],
    hidden: [code],
  });
  // The service closes an expired window itself.
  expect(closes).toEqual([{ link: LINK }]);
});

test("the phone page from the QR code walks the downloads, the fingerprint check and the trust steps", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const html = python(`import sys; from altitude import tls
sys.stdout.write(tls._guide({"name": "Altitude local CA", "sha256": ${JSON.stringify(FINGERPRINT)}}, "https://192.168.1.20:8890", 10).decode())`);
  const shown = async () => { await page.setContent(html); };
  await walk.state("12-phone-page", {
    action: shown,
    visible: [page.getByRole("heading", { name: "Add this phone to Altitude" }), page.getByText(/^10 17 1E 25 2C 33 3A 41/),
      page.getByRole("link", { name: "Download the profile" }), page.getByRole("link", { name: "Download the certificate" }),
      page.getByText("No Profile Downloaded?", { exact: true }),
      page.getByText("Settings › General › VPN & Device Management", { exact: true }),
      page.getByText(/iOS deletes an uninstalled profile after eight minutes/),
      page.getByText("Certificate Trust Settings", { exact: false }), page.getByRole("link", { name: "https://192.168.1.20:8890" }).first()],
    hidden: [],
  });
  await page.emulateMedia({ colorScheme: "dark" });
  await walk.state("13-phone-page-dark", { action: shown, visible: [page.getByRole("link", { name: "Download the profile" })], hidden: [] });
});
