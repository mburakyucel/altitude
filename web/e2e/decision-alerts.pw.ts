import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Decision alerts (issue #221) at both widths: the switch's states, one alert for a new decision,
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

/** A push service this browser can subscribe to; without one no device could be woken here. */
const fakePushService = (page: Page) =>
  page.addInitScript(() => {
    const subscription = { endpoint: "https://push.example/wake/walkthrough", unsubscribe: () => Promise.resolve(true) };
    PushManager.prototype.subscribe = () => Promise.resolve(subscription as unknown as PushSubscription);
    PushManager.prototype.getSubscription = () => Promise.resolve(subscription as unknown as PushSubscription);
  });

test("the switch walks its states and alerts once for each new decision", { tag: "@chromium" }, async ({ page, context, request }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const decision = async (project: string, title: string, question: string, escalated = true) => {
    const response = await request.post("/fixture/decision", { data: { project, title, question, escalated } });
    expect(response.ok()).toBe(true);
    return (await response.json()).slug as string;
  };
  const card = (title: string) => page.getByRole("article", { name: title });
  const offer = page.getByRole("button", { name: "Alert me about new decisions" });
  const on = page.getByRole("button", { name: "Alerts on" });

  await recordAlerts(page);
  await context.grantPermissions(["notifications"]);
  // A device push cannot wake is alerted by the open page; one push wakes is alerted by its worker (below).
  await page.route("**/api/alerts", (route) => route.fulfill({ json: { key: null } }));
  await walk.open("/");
  await walk.state("01-alerts-off", { visible: [offer, card("Choose backup retention")], hidden: [on] });

  await walk.state("02-alerts-on", {
    action: () => offer.click(),
    visible: [on, page.getByText(/only while Altitude is open/)], hidden: [offer],
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

  // Permission refused: the switch says so, and Needs you keeps working.
  await context.clearPermissions();
  await walk.open("/");
  await expect(offer).toBeEnabled();
  await offer.click();
  const blocked = page.getByText(/blocked in this browser's settings/);
  await walk.state("06-permission-blocked", { visible: [blocked, card("Choose backup retention")], hidden: [on] });
  await expect(offer).toBeDisabled();

  // Settings offers the same switch, disabled-looking, with the same reason on press and no choices.
  await walk.open("/settings");
  const notifications = page.getByRole("region", { name: "Notifications" });
  const blockedSwitch = notifications.getByRole("switch", { name: "Decision alerts" });
  await expect(blockedSwitch).toHaveAttribute("aria-disabled", "true");
  await walk.state("08-settings-blocked", {
    action: async () => {
      await blockedSwitch.scrollIntoViewIfNeeded();
      await blockedSwitch.click({ force: true }); // aria-disabled: the press reveals the reason
    },
    visible: [notifications.getByText(/blocked in this browser's settings/)], hidden: [notifications.getByRole("combobox")],
  });
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
  await walk.open("/");
  await page.getByRole("button", { name: "Alert me about new decisions" }).click();
  await expect(page.getByText(/even when Altitude is closed/)).toBeVisible();
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

test("a browser without notifications keeps every decision usable", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: undefined });
  });
  await walk.open("/");
  const offer = page.getByRole("button", { name: "Alert me about new decisions" });
  await walk.state("07-alerts-unavailable", {
    visible: [page.getByText("This browser cannot show alerts."), page.getByRole("article", { name: "Choose backup retention" })],
    hidden: [page.getByRole("button", { name: "Alerts on" })],
  });
  await expect(offer).toBeDisabled();
});

test("a push service that refuses alerts is named with its reason until a push gets through", { tag: "@chromium" }, async ({ page, context, request }, info) => {
  const walk = walkthrough(page, info);
  const tick = async (status: number, reason = "") =>
    (await request.post("/fixture/tick", { data: { status, reason } })).json();
  const offer = page.getByRole("button", { name: "Alert me about new decisions" });
  const on = page.getByRole("button", { name: "Alerts on" });
  const refused = page.getByText(/push\.example refused Altitude's last alert \(403 BadJwtToken\)/);
  const reach = page.getByText(/even when Altitude is closed/);

  await recordAlerts(page);
  await fakePushService(page);
  await context.grantPermissions(["notifications"]);
  await walk.open("/");
  await offer.click();
  await expect(reach).toBeVisible();
  const decision = await request.post("/fixture/decision", { data: { project: "beacon", title: "Pick a drill day", question: "Which day?" } });
  expect(decision.ok()).toBe(true);
  expect(await tick(403, "BadJwtToken")).toEqual({ refused: [{ host: "push.example", reason: "403 BadJwtToken" }] });

  // The page reads the refusal on its next return to the screen.
  await page.reload();
  await walk.state("09-push-refused", { visible: [on, refused], hidden: [reach, offer] });

  // Turning alerts off and on subscribes this device afresh; the line returns to its reach.
  await on.click();
  await expect(offer).toBeVisible();
  await walk.state("10-refused-then-resubscribed", { action: () => offer.click(), visible: [on, reach], hidden: [refused] });

  // A refusal cleared by the push service accepting the next alert stops showing too.
  await request.post("/fixture/decision", { data: { project: "beacon", title: "Pick a restore target", question: "Which host?" } });
  await tick(403, "BadJwtToken");
  await page.reload();
  await expect(refused).toBeVisible();
  await request.post("/fixture/decision", { data: { project: "beacon", title: "Pick a log format", question: "Which format?" } });
  expect(await tick(201)).toEqual({ refused: [] });
  await page.reload();
  await walk.state("11-refusal-cleared", { visible: [on, reach], hidden: [refused] });
});
