import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// This browser starts unpaired; the spec's own API calls act as an already paired device.
test.use({ paired: false });

const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";

/** A one-time code, made the way a paired device's Settings › Devices makes one. */
async function code(request: APIRequestContext): Promise<string> {
  const response = await request.post("/api/devices/code", { data: {} });
  expect(response.ok()).toBe(true);
  return (await response.json()).code as string;
}

function screen(page: Page) {
  return {
    heading: page.getByRole("heading", { name: "Pair this device" }),
    field: page.getByLabel("Pairing code"),
    pair: page.getByRole("button", { name: "Pair", exact: true }),
    alert: page.getByRole("alert"),
    removed: page.getByText("This device is no longer paired. Pair it again to continue."),
    app: page.getByRole("heading", { name: "Needs you" }),
  };
}

test("an unpaired browser sees only the pairing screen, and wrong and cancelled codes say why", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { heading, field, pair, alert, app } = screen(page);
  await walk.open("/projects");
  await walk.state("01-unpaired", {
    visible: [heading, field, page.getByText("alt pair", { exact: true }), page.getByText("Pair only after it opens without a warning.", { exact: false })],
    hidden: [alert, app],
  });
  await expect(pair).toBeDisabled();
  const cancelled = await code(request);
  await walk.state("02-wrong-code", {
    action: async () => { await field.fill("2222-2222"); await pair.click(); },
    visible: [page.getByText("That code is not right. 4 tries left.")], hidden: [app],
  });
  await walk.state("03-typing-clears-the-error", { action: () => field.fill("2222"), visible: [pair], hidden: [alert] });
  for (const left of ["3 tries", "2 tries", "1 try"]) {
    await field.fill("2222-2222");
    await pair.click();
    await expect(page.getByText(`That code is not right. ${left} left.`)).toBeVisible();
  }
  await walk.state("04-too-many-tries", {
    action: async () => { await field.fill("2222-2222"); await pair.click(); },
    visible: [page.getByText("Too many wrong codes, so this one is cancelled. Make a new one.")], hidden: [app],
  });
  await walk.state("05-cancelled-code", {
    action: async () => { await field.fill(cancelled); await pair.click(); },
    visible: [page.getByText("This code has expired or was already used. Make a new one.")], hidden: [app],
  });
});

test("a pairing link pairs the browser, and removing it in Settings returns it to the pairing screen", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { heading, removed, app } = screen(page);
  await walk.open(`/pair?code=${await code(request)}`);
  await walk.state("01-paired-by-link", { visible: [app], hidden: [heading] });
  await expect(page).toHaveURL(/\/$/);  // the code left the address bar
  await page.goto("/settings/devices");
  const list = page.getByRole("list", { name: "Paired devices" });
  const mine = list.getByRole("listitem").filter({ hasText: "This device" });
  await walk.state("02-devices", { visible: [list, mine, list.getByText("Playwright browser")], hidden: [heading] });
  const pairAnother = page.getByRole("region", { name: "Pair another device" });
  await walk.state("03-code-for-another-device", {
    action: () => page.getByRole("button", { name: "Make a pairing code" }).click(),
    visible: [pairAnother.getByLabel("Pairing code"), pairAnother.getByText(/\/pair\?code=/)], hidden: [],
  });
  await walk.state("04-confirm-removal", {
    action: () => mine.getByRole("button", { name: "Remove" }).click(),
    visible: [mine.getByText("This browser will need a new code to open Altitude again."), mine.getByRole("button", { name: "Cancel" })], hidden: [],
  });
  await walk.state("05-cancelled", {
    action: () => mine.getByRole("button", { name: "Cancel" }).click(),
    visible: [mine.getByRole("button", { name: "Remove" })], hidden: [mine.getByText("This browser will need a new code")],
  });
  await mine.getByRole("button", { name: "Remove" }).click();
  await walk.state("06-removed", {
    action: () => mine.getByRole("button", { name: "Remove" }).click(),
    visible: [heading, removed], hidden: [list],
  });
  const devices = await (await request.get("/api/devices")).json();
  expect(devices.devices.map((device: { name: string }) => device.name)).toEqual(["Playwright browser"]);
});

test("the pairing screen waits for Altitude and offers a retry when it cannot reach it", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const { heading } = screen(page);
  let release = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/access", async (route) => { await held; await route.abort(); });
  await page.goto("/");
  await walk.state("01-loading", { visible: [page.locator(".pair-screen[aria-busy='true']")], hidden: [heading] });
  release();
  const retry = page.getByRole("button", { name: "Retry" });
  await walk.state("02-unreachable", { visible: [page.getByText("Could not reach Altitude."), retry], hidden: [heading] });
  await page.unroute("**/api/access");
  await walk.state("03-retried", { action: () => retry.click(), visible: [heading], hidden: [retry] });
});

test.describe("on an iPhone", () => {
  test.use({ userAgent: IPHONE });

  test("Safari and the Home Screen app pair as separate devices @phone-only", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const { app } = screen(page);
    await walk.open(`/pair?code=${await code(request)}`);
    await expect(app).toBeVisible();
    // The Home Screen app keeps its own cookies apart from Safari's, so it pairs on its own and says so.
    // A fresh cookie jar and the standalone flag stand in for it.
    await page.context().clearCookies();
    await page.addInitScript(() => Object.defineProperty(navigator, "standalone", { value: true }));
    await page.goto(`/pair?code=${await code(request)}`);
    await expect(app).toBeVisible();
    await page.goto("/settings/devices");
    const list = page.getByRole("list", { name: "Paired devices" });
    await walk.state("01-iphone-devices", {
      visible: [list.getByText("Safari on iPhone"), list.getByText("Home Screen app on iPhone")], hidden: [],
    });
  });
});
