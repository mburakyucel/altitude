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
    visible: [card.getByText("Altitude local CA"), card.getByRole("button", { name: "Set up a device" }),
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
    hidden: [card.getByRole("button", { name: "Set up a device" })],
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

test("Settings › Devices › Set up a device shows the QR code with its time left, closes and reports refusals", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const card = page.getByRole("region", { name: "Certificate" });
  const add = card.getByRole("button", { name: "Set up a device" });
  const code = card.getByRole("img", { name: `QR code for ${LINK}` });
  const setup = card.getByRole("link", { name: "Open setup page" });
  await certificate(page, LIMITED);
  await walk.open("/settings/devices");
  await walk.state("05-set-up-a-device", { action: () => add.scrollIntoViewIfNeeded(), visible: [add], hidden: [code, setup, card.getByRole("alert")] });
  // The suite's service listens on loopback only, so its real answer is the refusal.
  await walk.state("06-refused", {
    action: () => add.click(),
    visible: [card.getByRole("alert").getByText(/configured for 127\.0\.0\.1, which only this computer can open/), add],
    hidden: [code, setup],
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
  let finishClose: () => void = () => undefined;
  const closing = new Promise<void>((resolve) => { finishClose = resolve; });
  await page.route("**/api/devices/share-close", async (route) => {
    if (closeFails) {
      closeFails = false;
      return route.fulfill({ status: 503, json: { error: "Could not reach Altitude." } });
    }
    await closing;
    closes.push(route.request().postDataJSON());
    await route.fulfill({ json: { closed: true } });
  });
  await walk.state("07-opening", {
    action: () => add.click(),
    visible: [card.getByRole("button", { name: "Opening…" })],
    hidden: [card.getByRole("alert"), code, setup],
  });
  await walk.state("08-qr-code", {
    action: async () => { release(); await card.getByRole("button", { name: "Close" }).scrollIntoViewIfNeeded(); },
    visible: [code, card.getByRole("timer").getByText(/^Closes in (10:00|9:5\d)$/), card.getByText(LINK),
      setup, card.getByText(/^Open setup on this device/), card.getByText(/^10 17 1E 25 2C 33 3A 41/)],
    hidden: [add, card.getByRole("alert")],
  });
  // The navigation is real; only the fictional private-network share destination is supplied here.
  const html = python(`import sys; from altitude import tls
sys.stdout.write(tls._guide({"name": "Altitude local CA", "sha256": ${JSON.stringify(FINGERPRINT)}}, "https://192.168.1.20:8890", 10).decode())`);
  await page.context().route(LINK, (route) => route.fulfill({ contentType: "text/html", body: html }));
  const originalUrl = page.url();
  const opened = page.waitForEvent("popup");
  await setup.click();
  const setupPage = await opened;
  await walkthrough(setupPage, info).state("08a-setup-new-tab", {
    visible: [setupPage.getByRole("heading", { name: "Set up this device for Altitude" }),
      setupPage.getByRole("link", { name: "Download the profile" })], hidden: [],
  });
  expect(setupPage.url()).toBe(LINK);
  expect(await setupPage.evaluate(() => window.opener)).toBeNull();
  expect(page.url()).toBe(originalUrl);
  expect(closeFails).toBe(true); // Opening setup did not send share-close.
  await setupPage.close();
  await walk.state("08b-original-tab-retained", {
    visible: [code, setup, card.getByRole("timer"), card.getByText("Altitude local CA"),
      card.getByText(/^10 17 1E 25 2C 33 3A 41/)], hidden: [add],
  });
  await walk.state("09-close-failed", {
    action: () => card.getByRole("button", { name: "Close" }).click(),
    visible: [code, setup, card.getByRole("alert").getByText("The link is still open: Could not reach Altitude."),
      card.getByRole("button", { name: "Close" })],
    hidden: [add, card.getByRole("status")],
  });
  await walk.state("09a-closing", {
    action: () => card.getByRole("button", { name: "Close" }).click(),
    visible: [card.getByRole("button", { name: "Closing…" }), code], hidden: [setup],
  });
  await walk.state("10-closed", {
    action: async () => { finishClose(); },
    visible: [card.getByRole("status").getByText("The link is closed."), add],
    hidden: [code, setup, card.getByRole("timer"), card.getByRole("alert")],
  });
  expect(closes).toEqual([{ link: LINK }]);
  seconds = 2;
  gate = null;
  await add.click();
  await expect(code).toBeVisible();
  await walk.state("11-expired", {
    visible: [card.getByRole("status").getByText("The link is closed."), add],
    hidden: [code, setup],
  });
  // The service closes an expired window itself.
  expect(closes).toEqual([{ link: LINK }]);
});

test("the setup page walks desktop and mobile downloads, fingerprint checks and trust instructions", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const html = python(`import sys; from altitude import tls
sys.stdout.write(tls._guide({"name": "Altitude local CA", "sha256": ${JSON.stringify(FINGERPRINT)}}, "https://192.168.1.20:8890", 10).decode())`);
  const shown = async () => { await page.setContent(html); };
  await walk.state("12-phone-page", {
    action: shown,
    visible: [page.getByRole("heading", { name: "Set up this device for Altitude" }), page.getByText(/^10 17 1E 25 2C 33 3A 41/),
      page.getByRole("link", { name: "Download the profile" }), page.getByRole("link", { name: "Download the certificate" }),
      page.getByText("No Profile Downloaded?", { exact: true }),
      page.getByText("Settings › General › VPN & Device Management", { exact: true }),
      page.getByText(/iOS deletes an uninstalled profile after eight minutes/),
      page.getByText("Certificate Trust Settings", { exact: false }), page.getByRole("link", { name: "https://192.168.1.20:8890" }).first()],
    hidden: [],
  });
  const navigation = page.getByRole("navigation", { name: "Device instructions" });
  for (const [device, id, instruction] of [
    ["Linux", "linux", "Local certificates › Custom › Installed by you › Trusted Certificates › Import"],
    ["macOS", "macos", "Secure Sockets Layer (SSL)"],
    ["iPhone or iPad", "ios", "Settings › Profile Downloaded"],
    ["Android", "android", "Settings › Security › Encryption & credentials › Install a certificate › CA certificate"],
  ]) {
    await walk.state(`14-${id}-instructions`, {
      action: async () => {
        await navigation.getByRole("link", { name: device, exact: true }).click();
        // Check the anchor before full-page capture temporarily changes the mobile viewport.
        await expect(page.locator(`#${id}`)).toBeInViewport();
      },
      visible: [page.locator(`#${id}`), page.getByText(instruction, { exact: true })], hidden: [],
    });
  }
  await walk.state("15-desktop-certificate-check", {
    action: () => page.getByRole("link", { name: "Download and check the certificate", exact: true }).click(),
    visible: [page.locator("#desktop"), page.getByRole("link", { name: "Download the certificate", exact: true }),
      page.getByText("openssl x509 -in ca.crt -noout -subject -fingerprint -sha256", { exact: true }),
      page.getByText(/This HTTP download page alone cannot prove/)], hidden: [],
  });
  await expect(page.getByRole("link", { name: "Download the certificate", exact: true })).toHaveAttribute("href", "/ca.crt");
  await expect(page.getByRole("link", { name: "Download the Android certificate" })).toHaveAttribute("href", "/ca.crt");
  await expect(page.getByRole("link", { name: "Download the profile" })).toHaveAttribute("href", "/altitude.mobileconfig");
  await walk.state("16-verify-before-pairing", {
    action: () => page.getByRole("link", { name: "verify HTTPS before pairing" }).first().click(),
    visible: [page.getByRole("heading", { name: "Verify HTTPS before pairing" }),
      page.getByText(/Compare this address with/), page.getByText(/Do not bypass a warning/),
      page.getByText("Settings › Devices › Pair another device", { exact: true }),
      page.getByText(/localhost refers to that device itself/)], hidden: [],
  });
  for (const link of await page.getByRole("link", { name: "https://192.168.1.20:8890", exact: true }).all()) {
    await expect(link).toHaveAttribute("href", "https://192.168.1.20:8890");
  }
  await page.emulateMedia({ colorScheme: "dark" });
  await walk.state("13-phone-page-dark", { action: shown, visible: [page.getByRole("link", { name: "Download the profile" })], hidden: [] });
});
