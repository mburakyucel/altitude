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
    trust: page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: /^Trust Altitude’s certificate/ }) }),
    field: page.getByLabel("Pairing code"),
    pair: page.getByRole("button", { name: "Pair", exact: true }),
    check: page.getByRole("button", { name: "Check again" }),
    alert: page.getByRole("alert"),
    removed: page.getByText("This device is no longer paired. Pair it again to continue."),
    app: page.getByRole("heading", { name: "Needs you" }),
  };
}

/** Pair on the screen with a code, as a person types it. */
async function pairWith(page: Page, value: string) {
  const { field, pair, app } = screen(page);
  await field.fill(value);
  await pair.click();
  await expect(app).toBeVisible();
}

type Trust = { local: boolean; https: boolean; check: boolean; certificate: { name: string } | null };
const DEVICE: Trust = { local: false, https: true, check: true, certificate: { name: "Altitude CA 4F7K" } };

/** This browser as another device on the network sees Altitude. The disposable service is plain-HTTP loopback, so
 * its access reply and trust check are answered here: each check takes the next answer, by default a refused
 * connection and then, as Altitude records a refusal, "not trusted"; `hold` keeps one checking. */
async function asDevice(page: Page, trust: Partial<Trust> = {}) {
  const view = { ...DEVICE, ...trust };
  const answers: ("trusted" | "retry")[] = [];
  let held: Promise<void> | null = null;
  let challenges = 0;
  const refused = new Set<string>();
  await page.route("**/api/access", (route) => route.fulfill({ json: { paired: false, device: null, trust: view } }));
  await page.route("**/api/trust", (route) => route.fulfill({ json: { challenge: `fixture-${++challenges}` } }));
  await page.route("**/api/trust/*", async (route) => {
    if (held) await held;
    const answer = answers.shift();
    if (answer) return route.fulfill({ json: answer === "trusted" ? { trusted: true } : { retry: true } });
    if (refused.has(route.request().url())) return route.fulfill({ json: { trusted: false } });
    refused.add(route.request().url());
    return route.abort("connectionrefused");
  });
  return {
    answers,
    hold: () => {
      let release = () => {};
      held = new Promise<void>((resolve) => { release = resolve; });
      return () => { held = null; release(); };
    },
  };
}

test("on the computer running Altitude, pairing needs only a code, and wrong and cancelled codes say why", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { heading, trust, field, pair, alert, app } = screen(page);
  await walk.open("/projects");
  await walk.state("01-local", {
    visible: [heading, page.getByText("This is the computer running Altitude."), trust.getByText("Trusted", { exact: true }),
      field, page.getByText("Enter the code from alt pair, or from Settings › Devices on a paired device.")],
    hidden: [alert, app, page.getByRole("link", { name: /^Download/ }), page.getByText(/without a warning/)],
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

test("the pairing code field places the dash itself as the code is typed, pasted or edited", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { field, pair, app } = screen(page);
  await walk.open("/");
  await expect(field).toHaveValue("");
  await walk.state("01-three-characters", { action: () => field.pressSequentially("abc"), visible: [field, pair], hidden: [app] });
  await expect(field).toHaveValue("ABC");
  await walk.state("02-four-characters", { action: () => field.press("d"), visible: [field], hidden: [app] });
  await expect(field).toHaveValue("ABCD-");
  await field.press("-");  // the dash is already there
  await expect(field).toHaveValue("ABCD-");
  await field.press("Backspace");  // it takes the fourth character, never the dash
  await expect(field).toHaveValue("ABC");
  await walk.state("03-complete", { action: () => field.pressSequentially("d-2345"), visible: [field], hidden: [app] });
  await expect(field).toHaveValue("ABCD-2345");
  await field.press("6");  // eight characters at most
  await expect(field).toHaveValue("ABCD-2345");
  // An edit inside the code reformats around it, and the caret stays at the edit.
  for (let step = 0; step < 7; step += 1) await field.press("ArrowLeft");  // to the B; Home is a scroll on macOS
  await field.press("Backspace");
  await expect(field).toHaveValue("ACD2-345");
  await field.press("B");
  await expect(field).toHaveValue("ABCD-2345");
  expect(await field.evaluate((input: HTMLInputElement) => input.selectionStart)).toBe(2);
  // A paste or autofill replaces the selection in one insertion, which WebKit's harness can do without a clipboard.
  for (const pasted of ["ABCD2345", "ABCD-2345", "abcd-2345", "  abcd-2345 "]) {
    await field.selectText();
    await page.keyboard.insertText(pasted);
    await expect(field, `pasting ${JSON.stringify(pasted)}`).toHaveValue("ABCD-2345");
  }
  // A typed code pairs whatever its case or dash.
  const typed = (await code(request)).replace("-", "").toLowerCase();
  let release = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/pair", async (route) => { await held; await route.continue(); });
  await field.fill("");
  await field.pressSequentially(typed);
  await expect(field).toHaveValue(`${typed.slice(0, 4)}-${typed.slice(4)}`.toUpperCase());
  const pairing = page.getByRole("button", { name: "Pairing…" });
  await walk.state("04-pairing", { action: () => pair.click(), visible: [pairing], hidden: [app] });
  await expect(pairing).toBeDisabled();
  await expect(field).toBeDisabled();
  release();
  await walk.state("05-paired", { visible: [app], hidden: [field] });
});

test("a code pairs the browser, and removing it in Settings returns it to the pairing screen", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { heading, field, pair, removed, app } = screen(page);
  await walk.open("/");
  const value = await code(request);
  await walk.state("01-paired-by-code", { action: async () => { await field.fill(value); await pair.click(); }, visible: [app], hidden: [heading] });
  await page.goto("/settings/devices");
  const list = page.getByRole("list", { name: "Paired devices" });
  const mine = list.getByRole("listitem").filter({ hasText: "This device" });
  await walk.state("02-devices", { visible: [list, mine, list.getByText("Playwright browser")], hidden: [heading] });
  const pairAnother = page.getByRole("region", { name: "Pair another device" });
  await walk.state("03-code-for-another-device", {
    action: () => page.getByRole("button", { name: "Make a pairing code" }).click(),
    visible: [pairAnother.getByLabel("Pairing code"), pairAnother.getByText(/^Works once, for the next 10 minutes\./), pairAnother.getByRole("button", { name: "Make a new code" })],
    hidden: [pairAnother.getByText(/pair\?code=/)],
  });
  const remove = mine.getByRole("button", { name: "Remove", exact: true });
  const confirm = mine.getByRole("group", { name: /^Remove .+\?$/ });
  await walk.state("04-confirm-removal", {
    action: () => remove.click(),
    visible: [mine.getByText("This browser will need a new code to open Altitude again."), confirm.getByRole("button", { name: "Remove device", exact: true })], hidden: [remove],
  });
  // Cancel comes first and takes focus, so Enter never removes by accident.
  await expect(confirm.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
  await expect(confirm.getByRole("button")).toHaveText(["Cancel", "Remove device"]);
  await walk.state("05-cancelled", {
    action: () => confirm.getByRole("button", { name: "Cancel", exact: true }).click(),
    visible: [remove], hidden: [mine.getByText("This browser will need a new code"), confirm],
  });
  await remove.click();
  await walk.state("05b-escaped", {
    action: () => page.keyboard.press("Escape"),
    visible: [remove], hidden: [mine.getByText("This browser will need a new code"), confirm],
  });
  await remove.click();
  await walk.state("06-removed", {
    action: () => confirm.getByRole("button", { name: "Remove device", exact: true }).click(),
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
    await walk.open("/");
    await pairWith(page, await code(request));
    // The Home Screen app keeps its own cookies apart from Safari's, so it pairs on its own and says so.
    // A fresh cookie jar and the standalone flag stand in for it.
    await page.context().clearCookies();
    await page.addInitScript(() => Object.defineProperty(navigator, "standalone", { value: true }));
    await page.goto("/");
    await pairWith(page, await code(request));
    await page.goto("/settings/devices");
    const list = page.getByRole("list", { name: "Paired devices" });
    await walk.state("01-iphone-devices", {
      visible: [list.getByText("Safari on iPhone"), list.getByText("Home Screen app on iPhone")], hidden: [],
    });
  });

  test("an iPhone on the network is shown the profile and the Certificate Trust Settings switch @phone-only", async ({ page }, info) => {
    const walk = walkthrough(page, info);
    const { trust, field } = screen(page);
    await asDevice(page);
    await walk.open("/");
    await walk.state("01-iphone-not-trusted", {
      visible: [trust.getByRole("link", { name: "Download the profile" }), trust.getByText(/^Open Settings › Profile Downloaded\./),
        trust.getByText("Turn it on: Settings › General › About › Certificate Trust Settings › “Altitude CA 4F7K”."),
        page.getByText("Not trusted yet. The usual missing step is the switch in Certificate Trust Settings.")],
      hidden: [trust.getByRole("link", { name: "Download the certificate" }), field],
    });
    await expect(trust.getByRole("link", { name: "Download the profile" })).toHaveAttribute("href", "/api/certificate/altitude.mobileconfig");
  });
});

test("a device on the network checks that it trusts Altitude's certificate before it pairs", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { heading, trust, field, check, app } = screen(page);
  const device = await asDevice(page);
  const phone = info.project.name === "phone";  // the phone project is an Android browser
  await walk.open("/");
  await walk.state("01-not-trusted", {
    visible: [heading, page.getByText("You opened Altitude’s HTTPS address."), trust.getByText("Not trusted yet", { exact: true }),
      trust.getByRole("link", { name: "Download the certificate" }), check,
      page.getByText(phone ? "Not trusted yet. Install the certificate as a CA certificate."
        : "Not trusted yet. Import the certificate as a trusted authority, then restart the browser.")],
    hidden: [field, trust.getByRole("link", { name: "Download the profile" }), trust.getByText(/^[0-9A-F]{2}( [0-9A-F]{2}){7}$/)],
  });
  if (!phone) await expect(trust.getByRole("link", { name: "Setup guide" })).toBeVisible();
  const release = device.hold();
  await walk.state("02-checking", { action: () => check.click(), visible: [trust.getByText("Checking…")], hidden: [field] });
  await expect(check).toBeDisabled();
  device.answers.push("trusted");
  await walk.state("03-trusted", {
    action: async () => release(),
    visible: [trust.getByText("Trusted", { exact: true }), field],
    hidden: [trust.getByRole("link", { name: "Download the certificate" }), check],
  });
  await page.unroute("**/api/access");
  const value = await code(request);
  await walk.state("04-paired", {
    action: async () => { await field.fill(value); await page.getByRole("button", { name: "Pair", exact: true }).click(); },
    visible: [app], hidden: [heading],
  });
});

test("a trust check that cannot finish says so, never that the device is untrusted", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const { trust, field, check } = screen(page);
  const device = await asDevice(page);
  device.answers.push("retry", "retry", "retry");
  await walk.open("/");
  await walk.state("01-could-not-check", {
    visible: [trust.getByText("Couldn’t check", { exact: true }), page.getByText("Couldn’t check.", { exact: true }), check],
    hidden: [page.getByText(/^Not trusted yet\./), field],
  });
});

test("over plain HTTP elsewhere, the screen says to pair on the computer running Altitude", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const { heading, trust, field } = screen(page);
  await asDevice(page, { https: false, check: false, certificate: null });
  await walk.open("/");
  await walk.state("01-plain-http", {
    visible: [heading, page.getByText("This Altitude serves plain HTTP. Pair on the computer running it.")], hidden: [trust, field],
  });
});

test("with an externally supplied certificate, the device confirms it by hand before pairing", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const { trust, field } = screen(page);
  await asDevice(page, { check: false, certificate: null });
  await walk.open("/");
  const proceed = page.getByRole("button", { name: "Continue" });
  await walk.state("01-external-certificate", {
    visible: [page.getByText("Altitude can’t check this automatically. Open this address in a new Private tab; if it loads without a warning, tap Continue."), proceed],
    hidden: [field, trust.getByRole("link", { name: /^Download/ })],
  });
  await walk.state("02-confirmed", { action: () => proceed.click(), visible: [trust.getByText("Trusted", { exact: true }), field], hidden: [proceed] });
});
