import { test } from "./fixtures";
import { expect, type Page } from "@playwright/test";
import { walkthrough } from "./walkthrough";

/**
 * Restart banner states (SPEC.md §3.13, slice 5). The running service has no restart pending on
 * demand, so every pending state is the real overview with its `restart` field set, named "-overlay";
 * absent is the real field pinned to null so the walk is deterministic. POST /api/restart is answered
 * here and never reaches the service: pressing Restart in this walk restarts nothing.
 */
type Json = Record<string, unknown>;

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
};

test("restart banner offers Restart at the narrow quiet point while workers run, and leaves when the new process answers", async ({ page }, info) => {
  // Six reloads with a full overview read each: longer than Playwright's default 30s on a slow poll.
  test.setTimeout(120_000);
  const walk = walkthrough(page, info);
  await page.route("**/api/restart", (route) => route.fulfill({ json: { ok: true, unit: "altitude-restart-walkthrough" } }));
  const banner = page.getByRole("status", { name: "Restart pending" });
  const phone = info.project.name === "phone";
  const details = page.getByRole("dialog", { name: "Update details" });
  const detailRoot = phone ? details : banner;
  const restart = banner.getByRole("button", { name: "Restart", exact: true });
  const what = detailRoot.getByText("Merged changes to the web app are waiting to activate.");
  const rule = detailRoot.getByText(/^Altitude restarts at the next quiet moment\./);
  const waiting = detailRoot.getByText(/Waiting for altitude\/walkthrough-task, altitude L3\.$/);
  const restarting = banner.getByText("Altitude is restarting…", { exact: true });
  const heading = page.getByRole("heading", { name: "Needs you", exact: true });

  await restartIs(page, null);
  await walk.open("/");
  await walk.state("01-absent", { visible: [heading], hidden: [banner] });

  await restartIs(page, { ...pending, waiting_for: [] });
  await walk.open("/");
  await walk.state("02-pending-quiet-overlay", { visible: [banner, restart, ...(phone ? [banner.getByText("Update ready")] : [what, rule])], hidden: [restarting, waiting] });
  if (phone) {
    await walk.state("02-update-details", { action: () => banner.getByRole("button", { name: "Details", exact: true }).click(), visible: [details, what, rule], hidden: [] });
    await details.getByRole("button", { name: "Close details" }).click();
  }
  // Above the header: before the phone header in the document, first in the main pane on the desktop.
  expect(await page.evaluate(() => {
    const status = document.querySelector('[role="status"][aria-label="Restart pending"]')!;
    const header = document.querySelector("header.phone-header");
    const main = document.querySelector("main")!;
    return header
      ? Boolean(status.compareDocumentPosition(header) & Node.DOCUMENT_POSITION_FOLLOWING) && !main.contains(status)
      : main.firstElementChild === status;
  }), "the banner sits above the header (SPEC.md §3.13)").toBe(true);
  expect(await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - document.documentElement.clientWidth),
    "no viewport scrolls horizontally with the banner up").toBeLessThanOrEqual(0);

  await restartIs(page, { ...pending, waiting_for: ["altitude/walkthrough-task", "altitude L3"] });
  await walk.open("/");
  await walk.state("03-pending-busy-overlay", { visible: [banner, ...(phone ? [banner.getByText("Update ready")] : [what, rule, waiting])], hidden: [restart, restarting] });
  if (phone) {
    await walk.state("03-waiting-details", { action: () => banner.getByRole("button", { name: "Details", exact: true }).click(), visible: [details, what, rule, waiting], hidden: [restart] });
    await details.getByRole("button", { name: "Close details" }).click();
  }

  await restartIs(page, { ...pending, waiting_for: [] });
  await walk.open("/");
  await expect(restart).toBeVisible();
  await walk.state("04-under-way-after-press-overlay", {
    action: () => restart.click(),
    visible: [banner, restarting], hidden: [restart, rule, waiting],
  });

  await restartIs(page, { ...pending, waiting_for: [], requested_at: new Date().toISOString(), unit: "altitude-restart-walkthrough" });
  await walk.open("/");
  await walk.state("05-under-way-requested-overlay", { visible: [banner, restarting], hidden: [restart, rule] });

  await restartIs(page, null);
  await walk.open("/");
  await walk.state("06-answered", { visible: [heading], hidden: [banner, restart, restarting] });
});

test("failed activation after an accepted restart stays actionable", async ({ page }, info) => {
  let failed = false;
  await page.route("**/api/overview*", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), restart: { ...pending, waiting_for: [], failed: failed ? "Activation did not complete" : null,
      requested_at: failed ? new Date().toISOString() : null } } });
  });
  await page.route("**/api/restart", (route) => { failed = true; return route.fulfill({ json: { ok: true } }); });
  const walk = walkthrough(page, info);
  await walk.open("/");
  const banner = page.getByRole("status", { name: "Restart pending" });
  const retry = banner.getByRole("button", { name: "Restart", exact: true });
  await walk.state("01-activation-failed-after-acceptance", { action: () => retry.click(),
    visible: [retry, banner.getByText(info.project.name === "phone" ? "Activation failed. L3 has the fault." : "Automatic activation did not complete; L3 has the fault.")],
    hidden: [banner.getByText("Altitude is restarting…", { exact: true })] });
});
