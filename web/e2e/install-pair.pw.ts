import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// Phone/desktop presentation on a disposable plain-HTTP loopback API. Access trust and managed
// certificate checks are presentation fixtures; pairing, devices, cookies and storage stay real.
// This file supplies no TLS/certificate-trust acceptance; install-pair-https.pw.ts owns that lane.
test.use({ paired: false });

type Trust = { local: boolean; https: boolean; check: boolean; certificate: { name: string } | null };
const LOCAL: Trust = { local: true, https: true, check: false, certificate: null };
const REMOTE: Trust = { local: false, https: true, check: true, certificate: { name: "Altitude CA DEMO" } };
const RECOVERY = "Run ~/.local/bin/alt pair on the computer running Altitude for a new code.";

function held() {
  let release!: () => void;
  const wait = new Promise<void>((resolve) => { release = resolve; });
  return { wait, release };
}

async function issue(request: APIRequestContext) {
  const response = await request.post("/api/devices/code", { data: {} });
  expect(response.ok()).toBe(true);
  return (await response.json()).code as string;
}

function controls(page: Page) {
  return {
    heading: page.getByRole("heading", { name: "Pair this device", exact: true }),
    field: page.getByLabel("Pairing code", { exact: true }),
    pair: page.getByRole("button", { name: "Pair", exact: true }),
    busy: page.getByRole("status", { name: "Pairing…", exact: true }),
    alert: page.getByRole("alert"),
    app: page.getByRole("heading", { name: "Needs you", exact: true }),
    check: page.getByRole("button", { name: "Check again", exact: true }),
  };
}

/** Preserve the real access/cookie observation, changing only how its trust row presents. */
async function presentation(page: Page, trust: Trust = LOCAL) {
  await page.route("**/api/access", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), trust } });
  });
}

function pairRequests(page: Page, secret: string) {
  const pairs: string[] = [];
  let leaked = false;
  page.on("request", (sent) => {
    if (new URL(sent.url()).pathname === "/api/pair") pairs.push(sent.postData() ?? "");
    else if (sent.url().includes(secret) || sent.postData()?.includes(secret)
      || Object.values(sent.headers()).some((value) => value.includes(secret))) leaked = true;
  });
  return { pairs, noLeak: () => expect(leaked).toBe(false) };
}

async function clean(page: Page, secret: string) {
  expect(new URL(page.url()).hash).toBe("");
  const exposed = await page.evaluate(() => JSON.stringify({
    local: { ...localStorage }, session: { ...sessionStorage }, history: history.state,
    text: document.body.textContent,
    fields: [...document.querySelectorAll("input")].map((input) => input.value),
  }));
  expect(exposed.includes(secret)).toBe(false);
}

async function devices(request: APIRequestContext) {
  const response = await request.get("/api/devices");
  expect(response.ok()).toBe(true);
  return (await response.json()).devices as { id: string; name: string }[];
}

test("install link shows only a busy cue, pairs once, and remains clean on reload and Back", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const ui = controls(page);
  const secret = await issue(request);
  const sent = pairRequests(page, secret);
  const before = await devices(request);
  await presentation(page);
  const finish = held();
  let redeemed = false;
  await page.route("**/api/pair", async (route) => {
    const response = await route.fetch();
    redeemed = true;
    await finish.wait;
    await route.fulfill({ response });
  });
  await walk.open(`/#pair=${secret}`);
  await walk.state("01-automatic-pairing", { visible: [ui.heading, ui.busy], hidden: [ui.field, ui.pair, ui.alert, ui.app] });
  await expect.poll(() => redeemed).toBe(true);
  await clean(page, secret);
  expect(sent.pairs).toHaveLength(1);
  await walk.state("02-paired-app", {
    action: async () => finish.release(), visible: [ui.app], hidden: [ui.heading, ui.busy, ui.field],
  });
  expect((await devices(request)).length).toBe(before.length + 1);
  await clean(page, secret);
  await page.reload();
  await expect(ui.app).toBeVisible();
  await page.goto("/settings/devices");
  await page.goBack();
  await expect(ui.app).toBeVisible();
  expect(sent.pairs).toHaveLength(1);
  await clean(page, secret);
  sent.noLeak();
});

test("install link waits through access loading and unreachable Retry before pairing", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const ui = controls(page);
  const secret = await issue(request);
  const sent = pairRequests(page, secret);
  await presentation(page);
  const loading = held();
  let unreachable = true;
  await page.route("**/api/access", async (route) => {
    if (unreachable) { await loading.wait; await route.abort("connectionrefused"); }
    else await route.fallback();
  });
  await walk.open(`/#pair=${secret}`);
  await walk.state("01-access-loading", { visible: [page.locator("main[aria-busy='true']")], hidden: [ui.heading, ui.field, ui.busy] });
  await clean(page, secret);
  expect(sent.pairs).toHaveLength(0);
  loading.release();
  const retry = page.getByRole("button", { name: "Retry", exact: true });
  await walk.state("02-access-unreachable", { visible: [page.getByText("Could not reach Altitude."), retry], hidden: [ui.field, ui.busy, ui.app] });
  expect(sent.pairs).toHaveLength(0);
  unreachable = false;
  await walk.state("03-access-retry-pairs", { action: () => retry.click(), visible: [ui.app], hidden: [retry, ui.heading, ui.field] });
  expect(sent.pairs).toHaveLength(1);
  await clean(page, secret);
  sent.noLeak();
});

for (const failure of ["malformed", "used", "wrong", "denied"] as const) {
  test(`install ${failure} link leaves blank manual recovery and typing removes its error`, async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const ui = controls(page);
    const issued = await issue(request);
    const secret = failure === "malformed" ? "invalid-token" : failure === "wrong"
      ? `${issued[0] === "2" ? "3" : "2"}${issued.slice(1)}` : issued;
    const sent = pairRequests(page, secret);
    const before = await devices(request);
    await presentation(page);
    if (failure === "used") {
      // Consume through real API/storage; the unpaired browser has an independent cookie jar.
      expect((await request.post("/api/pair", { data: { code: issued, standalone: false } })).ok()).toBe(true);
    }
    if (failure === "denied") {
      // A refusal presentation fixture, rather than a change to server authorization policy.
      await page.route("**/api/pair", (route) => route.fulfill({ status: 403, json: { error: "Pairing is denied." } }));
    }
    await walk.open(`/#pair=${secret}`);
    const error = failure === "malformed" ? "This pairing link is not valid."
      : failure === "used" ? "This pairing link has expired or was already used."
      : failure === "wrong" ? "That code is not right. 4 tries left." : "Pairing is denied.";
    await walk.state("01-link-recovery", { visible: [ui.heading, ui.field, ui.pair, ui.alert], hidden: [ui.busy, ui.app] });
    await expect(ui.alert).toHaveText(`${error} ${RECOVERY}`);
    await expect(ui.field).toHaveValue("");
    await expect(ui.pair).toBeDisabled();
    await clean(page, secret);
    expect(sent.pairs).toHaveLength(failure === "malformed" ? 0 : 1);
    expect((await devices(request)).length).toBe(before.length + (failure === "used" ? 1 : 0));
    await walk.state("02-typing-clears-error", { action: () => ui.field.fill("2"), visible: [ui.field, ui.pair], hidden: [ui.alert, ui.busy] });
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(sent.pairs).toHaveLength(failure === "malformed" ? 0 : 1);
    sent.noLeak();
    // Malformed/wrong/denied requests do not consume the valid code; it still pairs manually.
    if (failure === "denied") await page.unroute("**/api/pair");
    const manual = failure === "used" ? await issue(request) : issued;
    await walk.state("03-manual-recovery-pairs", {
      action: async () => { await ui.field.fill(manual); await ui.pair.click(); },
      visible: [ui.app], hidden: [ui.heading, ui.field, ui.alert],
    });
  });
}

test("already paired access discards an install link without a POST or another device", async ({ page, request, altitude }, info) => {
  const walk = walkthrough(page, info);
  const ui = controls(page);
  const secret = await issue(request);
  await page.context().addCookies([{ name: "altitude_device", value: altitude.device, url: altitude.url }]);
  const before = await devices(request);
  const sent = pairRequests(page, secret);
  await presentation(page);
  await walk.open(`/#pair=${secret}`);
  await walk.state("01-already-paired", { visible: [ui.app], hidden: [ui.heading, ui.field, ui.busy, ui.alert] });
  await clean(page, secret);
  expect(sent.pairs).toHaveLength(0);
  expect(await devices(request)).toEqual(before);
  sent.noLeak();
});

test("an unreadable successful pair reply observes the real cookie before showing recovery", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const ui = controls(page);
  const secret = await issue(request);
  const before = await devices(request);
  const sent = pairRequests(page, secret);
  let rechecked = false;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/access" && sent.pairs.length > 0) rechecked = true;
  });
  await presentation(page);
  await page.route("**/api/pair", async (route) => {
    const response = await route.fetch();
    // Preserve real Set-Cookie headers but lose the JSON body after redemption. The API wrapper
    // fails parsing; PairGate reconciles the cookie with the real access endpoint, without retry.
    await route.fulfill({ response, body: "" });
  });
  await walk.open(`/#pair=${secret}`);
  await walk.state("01-cookie-reconciled", { visible: [ui.app], hidden: [ui.heading, ui.field, ui.alert] });
  expect(rechecked).toBe(true);
  expect(sent.pairs).toHaveLength(1);
  expect((await devices(request)).length).toBe(before.length + 1);
  await clean(page, secret);
  sent.noLeak();
});

test("a failed transport reply rechecks access and exposes manual recovery without retrying", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const ui = controls(page);
  const secret = await issue(request);
  const sent = pairRequests(page, secret);
  let rechecked = false;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/access" && sent.pairs.length > 0) rechecked = true;
  });
  await presentation(page);
  await page.route("**/api/pair", (route) => route.abort("connectionrefused"));
  await walk.open(`/#pair=${secret}`);
  await walk.state("01-transport-recovery", { visible: [ui.heading, ui.field, ui.alert], hidden: [ui.busy, ui.app] });
  await expect(ui.alert).toHaveText("Could not confirm pairing. Reopen the install link, or run ~/.local/bin/alt pair for a new code.");
  await expect(ui.field).toHaveValue("");
  expect(rechecked).toBe(true);
  expect(sent.pairs).toHaveLength(1);
  await clean(page, secret);
  await walk.state("02-typing-clears-transport-error", { action: () => ui.field.fill("2"), visible: [ui.field], hidden: [ui.alert] });
  sent.noLeak();
});

for (const status of ["untrusted", "unknown"] as const) {
  test(`managed certificate ${status} and checking keep install link pending until trusted`, async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const ui = controls(page);
    const secret = await issue(request);
    const sent = pairRequests(page, secret);
    await presentation(page, REMOTE);
    await page.route("**/api/trust", (route) => route.fulfill({ json: { challenge: "fixture-check" } }));
    let answer: "untrusted" | "unknown" | "trusted" = status;
    let wait: Promise<void> | null = null;
    await page.route("**/api/trust/*", async (route) => {
      if (wait) await wait;
      await route.fulfill({ json: answer === "unknown" ? { retry: true } : { trusted: answer === "trusted" } });
    });
    await walk.open(`/#pair=${secret}`);
    const trust = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: /^Trust Altitude’s certificate/ }) });
    const pill = trust.getByText(status === "unknown" ? "Couldn’t check" : "Not trusted yet", { exact: true });
    const apple = /iPhone|iPad|iPod/.test(await page.evaluate(() => navigator.userAgent));
    const download = trust.getByRole("link", { name: apple ? "Download the profile" : "Download the certificate", exact: true });
    await walk.state(`01-${status}`, { visible: [ui.heading, pill, ui.check, download], hidden: [ui.field, ui.busy, ui.app] });
    expect(sent.pairs).toHaveLength(0);
    await clean(page, secret);
    if (apple) await expect(trust.getByText("Turn it on: Settings › General › About › Certificate Trust Settings › “Altitude CA DEMO”.")).toBeVisible();
    else if (info.project.name === "desktop") await expect(trust.getByText(/Restart the browser, then reopen the original private link from the installer\./)).toBeVisible();
    const checking = held();
    wait = checking.wait;
    await walk.state("02-checking", {
      action: () => ui.check.click(), visible: [trust.getByRole("status", { name: "Checking…", exact: true })], hidden: [ui.field, ui.busy, ui.app],
    });
    await expect(ui.check).toBeDisabled();
    expect(sent.pairs).toHaveLength(0);
    answer = "trusted";
    await walk.state("03-trusted-pairs", { action: async () => checking.release(), visible: [ui.app], hidden: [ui.heading, ui.field, ui.check, download] });
    expect(sent.pairs).toHaveLength(1);
    await clean(page, secret);
    sent.noLeak();
  });
}

test("external certificate confirmation releases the pending install link", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const ui = controls(page);
  const secret = await issue(request);
  const sent = pairRequests(page, secret);
  await presentation(page, { ...REMOTE, check: false, certificate: null });
  await walk.open(`/#pair=${secret}`);
  const proceed = page.getByRole("button", { name: "Continue", exact: true });
  await walk.state("01-external-confirmation", {
    visible: [ui.heading, proceed, page.getByText("Altitude can’t check this automatically. Open this address in a new Private tab; if it loads without a warning, tap Continue.")],
    hidden: [ui.field, ui.busy, ui.app, ui.check],
  });
  expect(sent.pairs).toHaveLength(0);
  await clean(page, secret);
  await walk.state("02-confirmation-pairs", { action: () => proceed.click(), visible: [ui.app], hidden: [ui.heading, ui.field, proceed] });
  expect(sent.pairs).toHaveLength(1);
  sent.noLeak();
});

test("remote plain HTTP never redeems an install link", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const ui = controls(page);
  const secret = await issue(request);
  const sent = pairRequests(page, secret);
  const before = await devices(request);
  await presentation(page, { ...REMOTE, https: false, check: false, certificate: null });
  await walk.open(`/#pair=${secret}`);
  await walk.state("01-remote-http", {
    visible: [ui.heading, page.getByText("This Altitude serves plain HTTP. Pair on the computer running it.")],
    hidden: [ui.field, ui.pair, ui.check, ui.busy, ui.app],
  });
  await clean(page, secret);
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  expect(sent.pairs).toHaveLength(0);
  expect(await devices(request)).toEqual(before);
  sent.noLeak();
});
