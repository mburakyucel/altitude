import { test } from "./fixtures";
import { expect, type Page } from "@playwright/test";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/** Presentation overlays retain the real fictional overview. Every restart POST is intercepted:
 * these walks prove UI states, never an actual service restart or backend transition. */
type Json = Record<string, unknown>;

// Finish overview overlays before teardown disposes their fetched responses (#380).
test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "wait" }); });

async function restartIs(page: Page, restart: Json | null) {
  await page.unroute("**/api/overview*");
  await page.route("**/api/overview*", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...((await response.json()) as Json), restart } });
  });
}

const pending = {
  since: new Date(Date.now() - 2 * 3600_000).toISOString(),
  head: "0000000",
  files: ["web/src/routes/Monitor.tsx", "web/src/shell/RestartBanner.tsx"],
  waiting_for: [],
};

test("dismissed update survives polling, navigation and refresh; new updates and failures can notify", async ({ page, request }, info) => {
  test.setTimeout(120_000);
  let restartPosts = 0;
  await page.route("**/api/restart", (route) => { restartPosts++; return route.fulfill({ json: { ok: true } }); });
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const notice = page.getByRole("status", { name: "Restart pending" });
  const dismiss = page.getByRole("button", { name: "Dismiss update notice" });
  const monitor = page.getByRole("region", { name: "Altitude update" });
  await restartIs(page, null);
  await walk.open(project.path);
  await walk.state("01-no-update", { visible: [page.getByRole("button", { name: "More actions" })], hidden: [notice] });

  await restartIs(page, pending);
  await page.reload();
  await walk.state("02-compact-pending-overlay", { visible: [notice, dismiss, notice.getByText("Update ready")], hidden: [notice.getByRole("button", { name: "Restart", exact: true })] });
  const composer = page.getByRole("textbox", { name: "Message" });
  await expect(composer).toBeVisible();
  await composer.fill("Draft remains usable with an update notice.");
  expect(await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  await walk.state("03-keyboard-dismissed", {
    action: async () => { await dismiss.focus(); await page.keyboard.press("Enter"); },
    visible: [composer], hidden: [notice],
  });
  await expect(composer).toHaveValue("Draft remains usable with an update notice.");
  // An actual ordinary overview poll must complete without recreating the notice.
  await page.waitForResponse((response) => response.url().includes("/api/overview") && response.ok(), { timeout: 30_000 });
  await expect(notice).toBeHidden();
  await page.getByRole("link", { name: "Monitor", exact: true }).click();
  await walk.state("04-dismissed-details-discoverable", { visible: [monitor, monitor.getByRole("button", { name: "Restart", exact: true })], hidden: [notice] });
  await page.goBack();
  await expect(composer).toBeVisible();
  await expect(notice).toBeHidden();
  await page.reload();
  await expect(composer).toBeVisible();
  await expect(notice).toBeHidden();

  await restartIs(page, { ...pending, waiting_for: ["altitude L3"], requested_at: new Date().toISOString() });
  await page.reload();
  await walk.state("05-status-change-stays-dismissed", { visible: [composer], hidden: [notice] });
  const next = { ...pending, head: "1111111" };
  await restartIs(page, next);
  await page.reload();
  await walk.state("06-new-update-overlay", { visible: [notice, dismiss], hidden: [] });
  await dismiss.click();
  await restartIs(page, { ...next, failed: "Build did not complete" });
  await page.reload();
  await walk.state("07-new-failure-overlay", { visible: [notice.getByText("Activation failed"), dismiss], hidden: [] });
  await dismiss.click();
  await page.reload();
  await expect(composer).toBeVisible();
  await expect(notice).toBeHidden();
  await restartIs(page, { ...next, failed: "Activation verification failed" });
  await page.reload();
  await walk.state("08-changed-failure-overlay", { visible: [notice.getByText("Activation failed")], hidden: [] });
  await dismiss.click();
  await restartIs(page, next);
  await page.reload();
  await walk.state("09-resolved-failure-stays-dismissed", { visible: [composer], hidden: [notice] });
  expect(restartPosts).toBe(0);
});

test("Monitor retains waiting, denied, requesting, accepted and failed update actions", async ({ page }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const notice = page.getByRole("status", { name: "Restart pending" });
  const status = page.getByRole("status", { name: "Update status" });
  const restart = status.getByRole("button", { name: "Restart", exact: true });
  const restarting = status.getByText("Altitude is restarting…", { exact: true });
  let mode = "denied";
  let release!: () => void;
  const requestGate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/restart", async (route) => {
    if (mode === "denied") return route.fulfill({ status: 403, json: { error: "Restart permission denied." } });
    if (mode === "pending") await requestGate;
    if (mode === "failed") await restartIs(page, { ...pending, requested_at: new Date().toISOString(), failed: "Activation did not complete" });
    return route.fulfill({ json: { ok: true } });
  });
  await restartIs(page, { ...pending, waiting_for: ["altitude/walkthrough-task", "altitude L3"] });
  await walk.open("/");
  await notice.getByRole("link", { name: "Update details in Monitor" }).click();
  const waiting = status.getByText(/Waiting for altitude\/walkthrough-task, altitude L3\.$/);
  await expect(restart).toHaveAttribute("aria-disabled", "true");
  await expect(waiting).toHaveClass("sr-only");
  await walk.state("01-waiting-details-overlay", { visible: [status, restart], hidden: [notice] });
  await restart.focus();
  await page.keyboard.press("Enter");
  await expect(waiting).not.toHaveClass("sr-only");
  await walk.state("01b-waiting-reason", { visible: [status, restart, waiting], hidden: [notice] });
  await restartIs(page, pending);
  await page.reload();
  await walk.state("02-quiet-point-overlay", { visible: [restart, status.getByRole("button", { name: "Restart", exact: true })], hidden: [notice, restarting] });
  await walk.state("03-request-denied-overlay", { action: () => restart.click(), visible: [restart, status.getByRole("alert").filter({ hasText: "Restart permission denied." })], hidden: [restarting] });
  const toast = page.getByRole("status").filter({ hasText: "Couldn't start the restart." });
  await expect(toast).toBeVisible();
  await toast.getByRole("button", { name: "Dismiss" }).click();
  await expect(toast).toBeHidden();
  await expect(status.getByRole("alert")).toBeVisible();
  mode = "pending";
  const accepted = page.waitForResponse((response) => response.url().endsWith("/api/restart") && response.ok());
  try {
    await walk.state("04-request-in-flight-overlay", { action: () => restart.click(), visible: [restarting], hidden: [restart, status.getByRole("alert")] });
  } finally { release(); }
  await accepted;
  await walk.state("05-request-accepted-overlay", { visible: [restarting], hidden: [restart] });
  await restartIs(page, { ...pending, requested_at: new Date().toISOString() });
  await page.reload();
  await walk.state("06-requested-status-overlay", { visible: [restarting], hidden: [restart, notice] });
  await restartIs(page, pending);
  await page.reload();
  // Keep the same mounted details component: a later failure must override mutation success.
  mode = "failed";
  await walk.state("07-failed-after-acceptance-overlay", { action: () => restart.click(), visible: [restart, status.getByText("Automatic activation did not complete; L3 has the fault.")], hidden: [restarting] });
  await restartIs(page, null);
  await page.reload();
  await walk.state("08-no-update", { visible: [page.getByText("No update pending.", { exact: true })], hidden: [status, restart, notice] });
});

test("Monitor update status has explicit loading and recoverable read-error states", async ({ page }, info) => {
  await page.route("**/api/restart", (route) => route.fulfill({ status: 403, json: { error: "No restart in this walkthrough." } }));
  let mode = "loading";
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/overview*", async (route) => {
    if (mode === "loading") await gate;
    if (mode === "error") return route.fulfill({ status: 503, json: { error: "Update status unavailable." } });
    const response = await route.fetch();
    return route.fulfill({ response, json: { ...await response.json(), restart: null } });
  });
  const walk = walkthrough(page, info);
  const section = page.getByRole("region", { name: "Altitude update" });
  const loading = section.getByRole("status", { name: "Loading update status…", exact: true });
  const error = section.getByText("Could not read update status.", { exact: false });
  await walk.open("/monitor");
  try {
    await walk.state("01-loading-overlay", { visible: [loading], hidden: [error, page.getByRole("button", { name: "Restart", exact: true })] });
  } finally { mode = "error"; release(); }
  await expect(error).toBeVisible({ timeout: 15_000 });
  await walk.state("02-read-error-overlay", { visible: [error, section.getByRole("button", { name: "Retry" })], hidden: [loading] });
  mode = "ready";
  await walk.state("03-recovered-empty", { action: () => section.getByRole("button", { name: "Retry" }).click(), visible: [section.getByText("No update pending.", { exact: true })], hidden: [error, loading] });
});
