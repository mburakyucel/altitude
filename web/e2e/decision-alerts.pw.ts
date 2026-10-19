import { expect, type Page, type TestInfo } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Decision alerts (issue #221) at both widths: the Needs you pill's states and the switch in Settings it opens, one
 * alert for a new decision,
 * none for a decision already on screen, none repeated after a refresh, and a push waking the real
 * service worker. The page's alerts are recorded at its calls into the worker's registration — the
 * last step before the operating system shows them; the worker's banners are read back from that
 * registration. A headless browser has no push service, so the subscription the page hands altd is a
 * fixture one, altd stores the real record, and the wake is delivered to the worker directly.
 */
test.use({ serviceScript: "decision-alerts-service.py" });

type Alert = { title: string; body?: string; url?: string; tag?: string };

const recordAlerts = (page: Page) =>
  page.addInitScript(() => {
    const seen: Alert[] = [];
    (window as unknown as { __alerts: Alert[] }).__alerts = seen;
    ServiceWorkerRegistration.prototype.showNotification = function (title: string, options?: NotificationOptions) {
      const data = options?.data as { url?: string } | undefined;
      seen.push({ title, body: options?.body, url: data?.url, tag: options?.tag });
      return Promise.resolve();
    };
  });

const alerts = (page: Page) => page.evaluate(() => (window as unknown as { __alerts: Alert[] }).__alerts);

/** The pill on the Needs you title row, naming this device's alerts and opening them in Settings. */
const pill = (page: Page, label: string) => page.getByRole("link", { name: `${label}, alert settings`, exact: true });

/** On the phone run, the same Needs you at 320 px: the title keeps one line and the pill keeps its row. */
async function narrow(page: Page, info: TestInfo, name: string) {
  if (info.project.name !== "phone") return;
  const before = page.viewportSize()!;
  await page.setViewportSize({ width: 320, height: 700 });
  const title = (await page.getByRole("heading", { name: "Needs you", level: 1 }).boundingBox())!;
  const link = (await page.locator(".needs-alerts").boundingBox())!;
  expect(title.height).toBeLessThan(48);
  expect(link.y).toBeLessThan(title.y + title.height);
  expect(link.x + link.width).toBeLessThanOrEqual(320);
  await page.screenshot({ path: info.outputPath(`${name}-320.png`), animations: "disabled" });
  await page.setViewportSize(before);
}

test("the pill stays on the title row while Needs you loads, fails to read and is empty", async ({ page }, info) => {
  test.setTimeout(60_000);
  const walk = walkthrough(page, info);
  const offer = pill(page, "Alerts off");
  let mode: "hold" | "fail" | "empty" = "hold";
  let release = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/overview*", async (route) => {
    if (mode === "hold") { await held; return route.abort(); }
    if (mode === "fail") return route.fulfill({ status: 500, json: { error: "down" } });
    const response = await route.fetch();
    return route.fulfill({ response, json: { ...(await response.json()), queue: [] } });
  });
  await page.goto("/");
  await walk.state("25-needs-loading", { visible: [offer, page.locator(".needs-page [aria-label=Loading]")], hidden: [] });
  mode = "fail";
  release();
  // The queue read retries before it reports the error.
  await expect(page.getByText(/^Could not read what needs you\./)).toBeVisible({ timeout: 30_000 });
  await walk.state("26-needs-read-error", { visible: [offer, page.getByText(/^Could not read what needs you\./)], hidden: [] });
  mode = "empty";
  await walk.state("27-needs-empty", {
    action: () => page.getByRole("button", { name: "Retry" }).click(),
    visible: [offer, page.getByText("Nothing needs you.", { exact: true })], hidden: [],
  });
});

/**
 * A push service this browser can subscribe to; without one no device could be woken here. Its subscription
 * outlives a reload and carries the key it was made with, as a browser's does, and each one after the first has
 * an endpoint of its own; `old` is one this device made before Altitude's key changed.
 */
const fakePushService = (page: Page, old?: string) =>
  page.addInitScript((old) => {
    type Stored = { endpoint: string; key: number[] } | null;
    const STORE = "fixture.push-subscription";
    if (old && localStorage.getItem(STORE) === null) localStorage.setItem(STORE, JSON.stringify({ endpoint: old, key: [9] }));
    const made = (stored: Stored) => stored && ({
      endpoint: stored.endpoint, options: { applicationServerKey: new Uint8Array(stored.key).buffer },
      unsubscribe: () => { localStorage.setItem(STORE, "null"); return Promise.resolve(true); },
    }) as unknown as PushSubscription;
    PushManager.prototype.subscribe = (options?: PushSubscriptionOptionsInit) => {
      const key = [...new Uint8Array(options!.applicationServerKey as Uint8Array)];
      const count = Number(localStorage.getItem(`${STORE}.count`) ?? 0) + 1;
      localStorage.setItem(`${STORE}.count`, String(count));
      const stored = { endpoint: `https://push.example/wake/walkthrough${count > 1 ? `-${count}` : ""}`, key };
      localStorage.setItem(STORE, JSON.stringify(stored));
      return Promise.resolve(made(stored)!);
    };
    PushManager.prototype.getSubscription = () => Promise.resolve(made(JSON.parse(localStorage.getItem(STORE) ?? "null") as Stored));
  }, old);

test("the switch walks its states and alerts once for each new decision", { tag: "@chromium" }, async ({ page, context, request }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const decision = async (project: string, title: string, question: string, escalated = true) => {
    const response = await request.post("/fixture/decision", { data: { project, title, question, escalated } });
    expect(response.ok()).toBe(true);
    return (await response.json()).slug as string;
  };
  const card = (title: string) => page.getByRole("article", { name: title });
  const offer = pill(page, "Alerts off");
  const on = pill(page, "Alerts while open");
  const alertsSwitch = page.getByRole("region", { name: "Notifications" }).getByRole("switch", { name: "Decision alerts" });
  const back = page.getByRole("button", { name: "‹ Back" });

  await recordAlerts(page);
  await context.grantPermissions(["notifications"]);
  // A device push cannot wake is alerted by the open page; one push wakes is alerted by its worker (below).
  await page.route("**/api/alerts", (route) => route.fulfill({ json: { key: null } }));
  await walk.open("/");
  await walk.state("01-alerts-off", { visible: [offer, card("Choose backup retention")], hidden: [on, alertsSwitch] });

  // The pill opens Settings at Notifications, where the switch lives; Back returns to the same Needs you visit,
  // a half-typed answer still in its box.
  const answer = card("Choose backup retention").getByRole("textbox", { name: /^Your answer to:/ });
  await answer.fill("Thirty days");
  await offer.click();
  await expect(alertsSwitch).toBeVisible();
  await back.click();
  await expect(answer).toHaveValue("Thirty days");
  await answer.fill("");
  await walk.state("02-settings-from-the-pill", {
    action: () => offer.click(),
    visible: [alertsSwitch, page.getByText("Altitude can alert you when a new decision arrives.")], hidden: [offer],
  });
  await alertsSwitch.click();
  await expect(page.getByText(/only while Altitude is open/)).toBeVisible();
  await walk.state("02-alerts-on", {
    action: () => back.click(),
    visible: [on, card("Choose backup retention")], hidden: [offer, page.getByText(/only while Altitude is open/)],
  });
  expect(await alerts(page)).toEqual([]); // the waiting decision is not announced as news

  // On screen in Needs you: the new card appears, and nothing pops up over it.
  await decision("beacon", "Run a restore drill", "Which drill first?");
  await walk.state("03-visible-decision-does-not-alert", { visible: [card("Run a restore drill"), on], hidden: [offer] });
  expect(await alerts(page)).toEqual([]);

  // Away from Needs you, inside another task: the decision alerts with its task name only.
  await page.getByRole("link", { name: "Run a restore drill" }).click();
  await expect(page.getByRole("heading", { name: /Run a restore drill/ })).toBeVisible();
  await decision("atlas", "Rotate the signing key", "Rotate now or at the next release?");
  await expect.poll(() => alerts(page)).toHaveLength(1);
  const [alert] = await alerts(page);
  expect(alert).toMatchObject({ title: "atlas needs a decision", body: "Rotate the signing key" });
  expect(alert!.url).toContain("/projects/atlas/tasks/rotate-the-signing-key");
  expect(JSON.stringify(alert)).not.toContain("Rotate now or at the next release?");
  await walk.state("04-alert-for-a-decision-out-of-sight", { visible: [page.getByRole("heading", { name: /Run a restore drill/ })], hidden: [] });

  // Out of sight: a headless Chromium tab stays visible behind another one, so the page reports the
  // hidden state a phone or a background desktop tab reports. The change stream must survive it.
  const visibility = (hidden: boolean) => page.evaluate((value) => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => value });
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => (value ? "hidden" : "visible") });
    document.dispatchEvent(new Event("visibilitychange"));
  }, hidden);
  await visibility(true);
  const archiving = await decision("atlas", "Archive the old drill logs", "Archive or keep them?", false);
  await expect.poll(() => alerts(page), { timeout: 30_000 }).toHaveLength(2);
  expect((await alerts(page))[1]).toMatchObject({ title: "atlas needs a decision", body: "Archive the old drill logs" });

  // Escalation republishes the decision that is already waiting: one decision, one alert.
  expect((await request.post("/fixture/escalate", { data: { project: "atlas", slug: archiving } })).ok()).toBe(true);
  await page.waitForTimeout(1_000);
  expect(await alerts(page)).toHaveLength(2);
  await visibility(false);

  // A refresh re-reads the same queue: the recorder starts empty again and stays empty.
  await page.reload();
  await expect(page.getByRole("heading", { name: /Run a restore drill/ })).toBeVisible();
  await page.waitForTimeout(1_000);
  expect(await alerts(page)).toEqual([]);
  await walk.state("05-refresh-repeats-nothing", { visible: [page.getByRole("heading", { name: /Run a restore drill/ })], hidden: [] });

  // Permission refused: the pill says so, and Needs you keeps working. A headless browser forgets a refusal on
  // reload, so the walk stays on the page: off in Settings, refused on again, Back.
  await context.clearPermissions();
  await page.evaluate(() => localStorage.removeItem("altitude.alerts"));
  await walk.open("/");
  await offer.click();
  await alertsSwitch.click();
  const blocked = page.getByText(/blocked in this browser's settings/);
  await expect(blocked).toBeVisible();
  await back.click();
  const blockedPill = pill(page, "Alerts blocked");
  await walk.state("06-permission-blocked", { visible: [blockedPill, card("Choose backup retention")], hidden: [on, blocked] });

  // Settings, opened from the pill, shows the switch disabled-looking with its reason already showing.
  await walk.state("08-settings-blocked", {
    action: () => blockedPill.click(),
    visible: [blocked, alertsSwitch], hidden: [page.getByRole("region", { name: "Notifications" }).getByRole("combobox")],
  });
  await expect(alertsSwitch).toHaveAttribute("aria-disabled", "true");
});

/** Every banner the device shows now, as the real service worker left them. Read only between wakes: Chromium
 * forgets a banner whose record it reads while the worker is still showing it. */
const banners = (page: Page) => page.evaluate(async () => {
  const registration = await navigator.serviceWorker.getRegistration("/sw.js");
  return ((await registration?.getNotifications()) ?? []).map((banner) => ({ title: banner.title, body: banner.body, tag: banner.tag }));
});

/** The push service's wake, delivered to the registered worker as the browser delivers it: no payload. It returns
 * once the browser records the worker's push event as completed, every banner of that wake shown. */
async function waker(page: Page) {
  const cdp = await page.context().newCDPSession(page);
  const registrations: { registrationId: string; scopeURL: string }[] = [];
  let completed = 0;
  cdp.on("ServiceWorker.workerRegistrationUpdated", ({ registrations: updated }) => registrations.push(...updated));
  cdp.on("BackgroundService.backgroundServiceEventReceived", ({ backgroundServiceEvent: event }) => {
    if (event.eventName === "Push event completed") completed += 1;
  });
  await cdp.send("ServiceWorker.enable");
  await cdp.send("BackgroundService.setRecording", { shouldRecord: true, service: "pushMessaging" });
  await cdp.send("BackgroundService.startObserving", { service: "pushMessaging" });
  await expect.poll(() => registrations.length).toBeGreaterThan(0);
  return async () => {
    const [registration] = registrations;
    const before = completed;
    await cdp.send("ServiceWorker.deliverPushMessage", {
      origin: new URL(page.url()).origin, registrationId: registration!.registrationId, data: "",
    });
    await expect.poll(() => completed).toBe(before + 1);
  };
}

test("a push wakes the real worker, which names the decision and never shows a bare banner", { tag: "@chromium" }, async ({ page, context, request }, info) => {
  const walk = walkthrough(page, info);
  await fakePushService(page);
  await context.grantPermissions(["notifications"]);
  await walk.open("/settings");
  await page.getByRole("switch", { name: "Decision alerts" }).click();
  await expect(page.getByText(/even when Altitude is closed/)).toBeVisible();
  await walk.open("/");
  // altd holds what waking this device takes, and nothing else about it.
  expect(await (await request.get("/fixture/push")).json())
    .toEqual({ subscriptions: ["https://push.example/wake/walkthrough"], every: {} });

  const wake = await waker(page);

  // The decision already waiting when alerts were turned on is not news; a wake with nothing new says what still
  // waits, never that nothing is new.
  const retention = { title: "atlas still needs a decision", body: "Choose backup retention", tag: expect.stringContaining("choose-backup-retention") };
  await wake();
  expect(await banners(page)).toEqual([retention]);

  // A new decision: the worker names its project and task, and the page, which push now covers, adds nothing.
  const slug = (await (await request.post("/fixture/decision", {
    data: { project: "beacon", title: "Pick a drill day", question: "Which day?" } })).json()).slug as string;
  await wake();
  const drill = { title: "beacon needs a decision", body: "Pick a drill day", tag: expect.stringContaining(slug) };
  expect(await banners(page)).toEqual([retention, drill]);
  await walk.state("12-push-names-the-decision", { visible: [page.getByRole("article", { name: "Pick a drill day" })], hidden: [] });

  // Answered elsewhere: the open page closes its banner without being touched.
  expect((await request.post("/fixture/answer", { data: { project: "beacon", slug } })).ok()).toBe(true);
  await expect.poll(() => banners(page), { timeout: 30_000 }).toEqual([retention]);
  await walk.state("13-answered-banner-closed", { visible: [page.getByRole("article", { name: "Choose backup retention" })], hidden: [page.getByRole("article", { name: "Pick a drill day" })] });

  // The reported night: the owner asks again after the reply, and the wake names it like a first ask.
  expect((await request.post("/fixture/ask-again", {
    data: { project: "beacon", slug, question: "Which day, given the freeze?" } })).ok()).toBe(true);
  await wake();
  expect(await banners(page)).toEqual([retention, drill]);
  await walk.state("14-asked-again-named", { visible: [page.getByRole("article", { name: "Pick a drill day" })], hidden: [] });

  // Nothing new: the wake names every decision still waiting and opens Needs you. No path shows a bare "Altitude".
  await wake();
  const waiting = { title: "Still waiting for you", body: "atlas · Choose backup retention\nbeacon · Pick a drill day", tag: "altitude-nothing-new" };
  expect((await banners(page)).sort((a, b) => a.title.localeCompare(b.title))).toEqual([retention, drill, waiting]);
  expect((await banners(page)).map((banner) => banner.title)).not.toContain("Altitude");
  await walk.state("15-still-waiting-named", { visible: [page.getByRole("article", { name: "Choose backup retention" }), page.getByRole("article", { name: "Pick a drill day" })], hidden: [] });
});

test("Settings chooses what this device's alerts show, how often they come and whether they sound", { tag: "@chromium" }, async ({ page, context, request }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const group = page.getByRole("region", { name: "Notifications" });
  const alertsSwitch = group.getByRole("switch", { name: "Decision alerts" });
  const show = group.getByRole("combobox", { name: "Show" });
  const often = group.getByRole("combobox", { name: "How often" });
  const sound = group.getByRole("switch", { name: "Sound" });
  const decision = async (project: string, title: string, question: string) =>
    expect((await request.post("/fixture/decision", { data: { project, title, question } })).ok()).toBe(true);

  await fakePushService(page);
  await context.grantPermissions(["notifications"]);
  await walk.open("/settings");
  await walk.state("16-settings-alerts-off", { action: () => group.scrollIntoViewIfNeeded(), visible: [alertsSwitch, group.getByText("Altitude can alert you when a new decision arrives.")], hidden: [show, often, sound] });
  await expect(alertsSwitch).not.toBeChecked();

  await walk.state("17-settings-alerts-on", {
    action: async () => {
      await alertsSwitch.click();
      await sound.scrollIntoViewIfNeeded();
    },
    visible: [group.getByText(/even when Altitude is closed/), show, often, sound,
      group.getByText(/^“atlas needs a decision” and the task name$/)],
    hidden: [group.getByText(/arrive together/)],
  });
  await expect(show).toHaveValue("task");
  await expect(often).toHaveValue("0");
  await expect(sound).toBeChecked();

  // Discreet: a locked screen learns only that a decision waits.
  await walk.state("18-settings-discreet", {
    action: () => show.selectOption({ label: "Only that a decision waits" }),
    visible: [group.getByText("“A decision needs you”")], hidden: [group.getByText(/and the task name/)],
  });
  // Paced: altd learns how often it may wake this device, and nothing else about it.
  await walk.state("19-settings-paced", {
    action: () => often.selectOption({ label: "At most every 15 minutes" }),
    visible: [group.getByText("Decisions in between arrive together in the next alert.")], hidden: [],
  });
  await expect.poll(async () => (await (await request.get("/fixture/push")).json()).every)
    .toEqual({ "https://push.example/wake/walkthrough": 900 });
  await walk.state("20-settings-silent", {
    action: async () => {
      await sound.click();
      await expect(sound).not.toBeChecked();
    },
    visible: [sound], hidden: [group.getByText("Saving…")],
  });

  // Altitude refuses a new pace: the reason shows, and the device keeps the pace Altitude still has.
  const refusal = "This device is not subscribed to alerts. Turn alerts off and on to subscribe it again.";
  await page.route("**/api/alerts/pace", (route) => route.fulfill({ status: 400, json: { error: refusal } }));
  await walk.state("21-settings-pace-refused", {
    action: () => often.selectOption({ label: "At most once an hour" }),
    visible: [group.getByRole("alert").filter({ hasText: refusal })], hidden: [group.getByText("Saving…")],
  });
  await expect(often).toHaveValue("900");
  await page.unroute("**/api/alerts/pace");

  // The worker reads the same choices: the waiting decision is not news, two new ones arrive in one banner
  // that names neither, and a reload keeps every choice.
  const wake = await waker(page);
  await decision("beacon", "Pick a drill day", "Which day?");
  await decision("atlas", "Rotate the signing key", "Rotate now or at the next release?");
  await wake();
  expect(await banners(page)).toEqual([{ title: "2 decisions need you", body: "", tag: expect.stringContaining("pick-a-drill-day") }]);
  expect(JSON.stringify((await banners(page)).map(({ title, body }) => [title, body]))).not.toMatch(/beacon|atlas|drill|signing/i);
  await page.reload();
  await expect(show).toHaveValue("none");
  await expect(often).toHaveValue("900");
  await expect(sound).not.toBeChecked();

  // One decision, discreet: the banner says a decision waits and opens it.
  await often.selectOption({ label: "Every new decision" });
  await expect.poll(async () => (await (await request.get("/fixture/push")).json()).every).toEqual({});
  await decision("beacon", "Pick a log format", "Which format?");
  await wake();
  expect((await banners(page)).map((banner) => banner.title)).toContain("A decision needs you");
  await walk.state("22-discreet-banner", { visible: [show], hidden: [] });

  // Off: the choices step aside until alerts are on again here.
  await walk.state("23-settings-alerts-off-again", { action: () => alertsSwitch.click(), visible: [alertsSwitch], hidden: [show, often, sound] });
});

test("a browser without notifications keeps every decision usable, and Settings says why", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: undefined });
  });
  await walk.open("/");
  const unavailable = pill(page, "Alerts unavailable");
  await walk.state("07-alerts-unavailable", {
    visible: [unavailable, page.getByRole("article", { name: "Choose backup retention" })],
    hidden: [page.getByText("This browser cannot show alerts.")],
  });
  await narrow(page, info, "07-alerts-unavailable");
  await walk.state("07-settings-unavailable", {
    action: () => unavailable.click(),
    visible: [page.getByText("This browser cannot show alerts.")], hidden: [unavailable],
  });
});

test("a refusal shows on its own device until a push gets through, and an ended subscription renews itself", { tag: "@chromium" }, async ({ page, context, request }, info) => {
  const walk = walkthrough(page, info);
  const tick = async (status: number, reason = "") =>
    (await request.post("/fixture/tick", { data: { status, reason } })).json();
  const ask = async (title: string) =>
    expect((await request.post("/fixture/decision", { data: { project: "beacon", title, question: "Which one?" } })).ok()).toBe(true);
  const subscriptions = async () => (await (await request.get("/fixture/push")).json()).subscriptions;
  const alertsSwitch = page.getByRole("switch", { name: "Decision alerts" });
  const on = pill(page, "Alerts on");
  const refusedPill = pill(page, "Push refused");
  const refused = page.getByText("The push service at push.example refused Altitude's last alert (403 BadJwtToken), so this "
    + "device gets alerts only while Altitude is open. Give Altitude a real contact address in ALTITUDE_PUSH_CONTACT on "
    + "the computer it runs on.");
  const reach = page.getByText(/even when Altitude is closed/);
  const device = "https://push.example/wake/walkthrough";

  await recordAlerts(page);
  await fakePushService(page);
  await context.grantPermissions(["notifications"]);
  await walk.open("/settings");
  await alertsSwitch.click();
  await expect(reach).toBeVisible();
  await ask("Pick a drill day");
  expect(await tick(403, "BadJwtToken")).toEqual({ subscriptions: [device], refused: { [device]: "403 BadJwtToken" } });

  // The device learns its refusal the next time Altitude opens there: two red words on Needs you, the whole
  // line with what to do in Settings.
  await walk.open("/");
  await walk.state("09-push-refused", { visible: [refusedPill], hidden: [on, refused, reach] });
  await expect(refusedPill).toHaveAttribute("data-tone", "danger");
  await narrow(page, info, "09-push-refused");
  await walk.state("09-settings-push-refused", { action: () => refusedPill.click(), visible: [refused], hidden: [reach] });

  // A refusal cleared by the push service accepting the next alert stops showing.
  await ask("Pick a log format");
  expect(await tick(201)).toEqual({ subscriptions: [device], refused: {} });
  await walk.open("/");
  await walk.state("10-refusal-cleared", { visible: [on], hidden: [refusedPill] });

  // A subscription the push service will never take again ends on Altitude's side, and the device makes a new one
  // the next time Altitude opens there, without the switch.
  await ask("Pick a restore target");
  expect(await tick(410)).toEqual({ subscriptions: [], refused: {} });
  await page.reload();
  await expect.poll(subscriptions).toEqual([`${device}-2`]);
  await walk.state("11-ended-subscription-renewed", { visible: [on], hidden: [refusedPill] });
});

test("a device subscribed with an old key renews itself when Altitude opens there", { tag: "@chromium" }, async ({ page, context, request }, info) => {
  const walk = walkthrough(page, info);
  const old = "https://push.example/wake/old-key";
  await fakePushService(page, old);
  await page.addInitScript(() => { // alerts were turned on here before Altitude's key changed
    if (localStorage.getItem("altitude.alerts") !== null) return;
    localStorage.setItem("altitude.alerts", "on");
    localStorage.setItem("altitude.alerts.push", "on");
  });
  await context.grantPermissions(["notifications"]);
  expect((await request.post("/api/alerts/subscription", { data: { endpoint: old } })).ok()).toBe(true);

  await walk.open("/");
  // Altitude forgets the endpoint the device replaced, and holds the new one.
  await expect.poll(async () => (await (await request.get("/fixture/push")).json()).subscriptions)
    .toEqual(["https://push.example/wake/walkthrough"]);
  await walk.state("24-old-key-renewed", { visible: [pill(page, "Alerts on")], hidden: [pill(page, "Push refused")] });
});
