import { test } from "./fixtures";
import { expect, type Page } from "@playwright/test";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/** Presentation overlays on the real fictional overview. Every update POST is intercepted: these walks
 * prove the notice's and Settings' states, never a download, install or service restart. */
type Json = Record<string, unknown>;

test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "wait" }); });

const NOTES = "https://github.com/example/altitude/releases/tag/v0.2.0";
const installed = { current: "v0.1.0", available: null, check: true, command: "alt update", checked: "2026-10-01T08:00:00Z", attempt: null };
const available = { ...installed, available: { version: "v0.2.0", notes: NOTES } };

/** Every overview read carries `state.update`, which a test changes as the daemon would. */
async function overlay(page: Page, state: { update: Json | null }) {
  await page.route("**/api/overview*", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...((await response.json()) as Json), update: state.update } });
  });
}

test("a newer release is offered, confirmed, installed or failed, and dismissed per version", async ({ page, request }, info) => {
  test.setTimeout(120_000);
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const state: { update: Json | null } = { update: null };
  const posts: unknown[] = [];
  let refuse = true;
  await page.route("**/api/update", async (route) => {
    posts.push(route.request().postDataJSON());
    if (refuse) return route.fulfill({ status: 403, json: { error: "Update requests must come from Altitude's own page." } });
    state.update = { ...available, attempt: { version: "v0.2.0", state: "running" } };
    return route.fulfill({ json: { update: state.update } });
  });
  await overlay(page, state);
  const notice = page.getByRole("status", { name: "New version" });
  const update = notice.getByRole("button", { name: "Update", exact: true });
  const install = notice.getByRole("button", { name: "Install v0.2.0" });
  const cancel = notice.getByRole("button", { name: "Cancel" });
  const dismiss = notice.getByRole("button", { name: "Dismiss new version notice" });
  const composer = page.getByRole("textbox", { name: "Message" });

  await walk.open(project.path);
  await walk.state("01-source-deployment-no-notice", { visible: [composer], hidden: [notice] });

  state.update = available;
  await page.reload();
  await walk.state("02-available", {
    visible: [notice.getByText("Altitude v0.2.0 is available."), notice.getByRole("link", { name: "What’s new" }), update, dismiss],
    hidden: [install, cancel],
  });
  await expect(notice.getByRole("link", { name: "What’s new" })).toHaveAttribute("href", NOTES);
  expect(await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);

  await walk.state("03-confirm", {
    action: () => update.click(),
    visible: [notice.getByText(/Install Altitude v0.2.0\? Altitude checks the download, then restarts\. If v0.2.0 does not start, v0.1.0 comes back\./), install, cancel],
    hidden: [update, dismiss],
  });
  await cancel.click();
  await expect(update).toBeVisible();
  await expect(install).toBeHidden();
  expect(posts).toEqual([]);

  await update.click();
  await walk.state("04-refused", {
    action: () => install.click(),
    visible: [notice.getByRole("alert").getByText("Update requests must come from Altitude's own page."), install, cancel],
    hidden: [],
  });

  refuse = false;
  await walk.state("05-installing", {
    action: () => install.click(),
    visible: [notice.getByText("Installing Altitude v0.2.0… Altitude restarts when it is ready.")],
    hidden: [install, cancel, update, dismiss, notice.getByRole("alert")],
  });
  expect(posts).toEqual([{ version: "v0.2.0" }, { version: "v0.2.0" }]);
  await page.reload();
  await expect(notice.getByText("Installing Altitude v0.2.0…")).toBeVisible();

  state.update = { ...available, attempt: { version: "v0.2.0", state: "failed", error: "Run alt update in a terminal to see why." } };
  await page.reload();
  await walk.state("06-failed", {
    visible: [notice.getByText(/The update to v0.2.0 did not finish\. Run alt update in a terminal to see why\. Altitude v0.1.0 keeps running\./),
      notice.getByRole("button", { name: "Try again" }), dismiss],
    hidden: [update],
  });
  await dismiss.click();
  await expect(notice).toBeHidden();
  await page.reload();
  await walk.state("07-failure-dismissed", { visible: [composer], hidden: [notice] });

  state.update = available;
  await page.reload();
  await expect(update).toBeVisible();
  await dismiss.click();
  await page.reload();
  await walk.state("08-version-dismissed", { visible: [composer], hidden: [notice] });

  state.update = { ...installed, available: { version: "v0.3.0", notes: NOTES.replace("v0.2.0", "v0.3.0") } };
  await page.reload();
  await walk.state("09-newer-version-notifies-again", { visible: [notice.getByText("Altitude v0.3.0 is available.")], hidden: [] });

  state.update = { ...installed, current: "v0.3.0" };
  await page.reload();
  await walk.state("10-updated-no-notice", { visible: [composer], hidden: [notice] });
});

test("Settings shows the version, the command that installs a newer one and the check switch", async ({ page }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const state: { update: Json | null } = { update: null };
  const switches: unknown[] = [];
  await page.route("**/api/update-check", async (route) => {
    const { enabled } = route.request().postDataJSON() as { enabled: boolean };
    switches.push(enabled);
    const machine = (await (await page.request.get("/api/machine")).json()) as Json;
    state.update = enabled ? available : { ...installed, check: false };
    return route.fulfill({ json: { ...machine, update_check: enabled, update: state.update } });
  });
  await overlay(page, state);
  const version = page.locator(".settings-version");
  const toggle = page.getByRole("switch", { name: /Check for new versions/ });

  await walk.open("/settings");
  await walk.state("11-settings-source-deployment", { visible: [page.getByRole("switch", { name: /Terminal/ })], hidden: [version, toggle] });

  state.update = { ...installed };
  await page.reload();
  await walk.state("12-settings-up-to-date", { visible: [version.getByText("v0.1.0 · Up to date"), toggle], hidden: [version.getByRole("button", { name: "Copy" })] });
  await expect(toggle).toBeChecked();

  state.update = available;
  await page.reload();
  await walk.state("13-settings-available", {
    visible: [version.getByText(/v0.1.0 · v0.2.0 is available/), version.getByRole("link", { name: "What’s new" }), version.getByText("alt update", { exact: true }),
      version.getByRole("button", { name: "Copy" }), page.getByRole("status", { name: "New version" })],
    hidden: [],
  });
  expect(await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);

  await walk.state("14-settings-check-off", {
    action: () => toggle.click(),
    visible: [version.getByText("v0.1.0", { exact: true }), page.getByText(/Twice a day Altitude asks GitHub for the latest release/)],
    hidden: [version.getByRole("button", { name: "Copy" }), page.getByRole("status", { name: "New version" })],
  });
  await expect(toggle).not.toBeChecked();
  await toggle.click();
  await expect(toggle).toBeChecked();
  await expect(page.getByRole("status", { name: "New version" })).toBeVisible();
  expect(switches).toEqual([false, true]);
});
