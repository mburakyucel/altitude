import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Decision alerts (issue #221) at both widths: the switch's states, one alert for a new decision,
 * none for a decision already on screen, and none repeated after a refresh. A headless browser has
 * no notification platform, so the walkthrough records the page's own calls into the service
 * worker's registration — the last step before the operating system shows the alert. It has no push
 * service either, so the subscription the page hands altd is a fixture one; altd stores the real record.
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

test("the switch walks its states and alerts once for each new decision", async ({ page, context, request }, info) => {
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
  await fakePushService(page);
  await context.grantPermissions(["notifications"]);
  await walk.open("/");
  await walk.state("01-alerts-off", { visible: [offer, card("Choose backup retention")], hidden: [on] });

  await walk.state("02-alerts-on", {
    action: () => offer.click(),
    visible: [on, page.getByText(/even when Altitude is closed/)], hidden: [offer],
  });
  expect(await alerts(page)).toEqual([]); // the waiting decision is not announced as news
  // altd holds what waking this device takes, and nothing else about it.
  expect(await (await request.get("/fixture/push")).json())
    .toEqual({ subscriptions: ["https://push.example/wake/walkthrough"] });

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

test("a device that cannot be woken keeps alerting while Altitude is open, and says so", async ({ page, context }, info) => {
  const walk = walkthrough(page, info);
  await recordAlerts(page);
  await context.grantPermissions(["notifications"]);
  // No key on the machine: nothing can wake this device, and the switch says what alerts it does give.
  await page.route("**/api/alerts", (route) => route.fulfill({ json: { key: null } }));
  await walk.open("/");
  await page.getByRole("button", { name: "Alert me about new decisions" }).click();
  await walk.state("08-alerts-on-without-push", {
    visible: [page.getByRole("button", { name: "Alerts on" }), page.getByText(/only while Altitude is open/)],
    hidden: [page.getByRole("button", { name: "Alert me about new decisions" })],
  });
});
