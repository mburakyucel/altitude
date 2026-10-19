import { expect, type Locator, type Page, type Route } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Improvement suggestions in a project's settings (design/wireframes/improvement-review/PROPOSAL.md, "Turning it on"):
 * through the real handlers and records, the Weekly switch and Get suggestions now open the sheet before the
 * operator's agreement; Turn on agrees and completes the press; a running review replaces the button with its line
 * and Stop; a skipped week and an unavailable button say why; a change of terms turns Weekly off and says so in chat.
 */
test.use({ serviceScript: "improvement-settings-service.py" });

/** Holds every settings press until released, so its saving state can be captured; release waits for the press to arrive. */
async function hold(page: Page, reply?: (route: Route) => Promise<void>) {
  let arrive = () => {};
  const arrived = new Promise<void>((resolve) => { arrive = resolve; });
  let release = () => {};
  const released = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/improve", async (route) => {
    arrive();
    await released;
    await (reply ? reply(route) : route.continue());
  });
  return async () => {
    await arrived;
    release();
    await page.unrouteAll({ behavior: "wait" });
  };
}

test("the sheet comes first, Turn on completes the press, and each Weekly and run state says where it stands", async ({ page }, info) => {
  test.setTimeout(120_000);
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const press = (target: Locator) => (phone ? target.tap() : target.click());
  const area = page.getByRole("region", { name: "Improvement suggestions", exact: true });
  const weekly = area.getByRole("switch", { name: /^Weekly/ });
  const now = area.getByRole("button", { name: "Get suggestions now", exact: true });
  const sheet = page.getByRole("dialog", { name: "Turn on improvement suggestions for altitude?" });
  const turnOn = sheet.getByRole("button", { name: "Turn on", exact: true });
  const cancel = sheet.getByRole("button", { name: "Cancel", exact: true });
  const offLine = area.getByText("Every Monday morning, when at least 5 tasks have finished.", { exact: true });
  const next = area.getByText(/^Next: Monday, \w{3} \d+\.$/);
  const reads = area.getByText(/^Reads the 6 tasks finished since \w{3} \d+\.$/);
  const sent = area.getByText("Counts and short chat quotes · Claude Sonnet 5.5 on your plan · capped per run, plus one L3 check", { exact: true });
  const running = area.getByText("Reading 6 tasks", { exact: true });
  const stop = area.getByRole("button", { name: "Stop", exact: true });

  await walk.open("/settings/projects/altitude");
  await area.scrollIntoViewIfNeeded();
  await walk.state("01-off-before-agreement", {
    visible: [area.getByRole("heading", { name: "Improvement suggestions" }),
      area.getByText("Finds friction that keeps coming back in this project's tasks and suggests fixes.", { exact: true }),
      weekly, offLine, now, reads, sent],
    hidden: [sheet, running, next],
  });
  await expect(weekly).not.toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  // Either control opens the sheet first; Cancel and Escape change nothing.
  await press(weekly);
  await walk.state("02-sheet-from-weekly", {
    visible: [sheet, sheet.getByText(/^Claude Sonnet 5\.5 reads counts from this project's tasks and short quotes from its chats \(up to 300 characters each\)\./),
      sheet.getByText(/Nothing is published, and nothing becomes work until you choose it\.$/), turnOn, cancel],
    hidden: [next],
  });
  await expect(cancel).toBeFocused();
  await expect(page.locator(".overlay")).toHaveAttribute("data-side", phone ? "bottom" : "center");
  await press(cancel);
  await walk.state("03-cancel-changes-nothing", { visible: [offLine, now], hidden: [sheet] });
  await expect(weekly).not.toBeChecked();
  await press(now);
  await expect(sheet).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(running).toBeHidden();

  // Turn on agrees and turns Weekly on at once: Saving… with the controls disabled, then its next Monday.
  await press(weekly);
  const saved = await hold(page);
  await press(turnOn);
  // The line describes the saved schedule, so it waits for the save rather than describing Weekly off.
  await walk.state("04-turn-on-saving", { visible: [area.getByText("Saving…", { exact: true })], hidden: [sheet, offLine, next] });
  await expect(weekly).toBeChecked();
  await expect(weekly).toBeDisabled();
  await saved();
  await walk.state("05-weekly-on", { visible: [next, now, reads], hidden: [offLine, area.getByText("Saving…", { exact: true })] });
  await expect(weekly).toBeChecked();

  // Agreed now: Get suggestions now starts a review without the sheet, and its line with Stop replaces the button.
  // From the press Get suggestions now reads Starting… until the run is recorded.
  const started = await hold(page);
  await press(now);
  await walk.state("05b-starting-at-the-press", { visible: [area.getByRole("button", { name: "Starting…", exact: true })], hidden: [now] });
  await started();
  await walk.state("06-running-replaces-the-button", { visible: [running, stop, next], hidden: [now, reads, sheet] });
  await press(stop);
  await walk.state("07-stopped-button-returns", { visible: [now, reads], hidden: [running, stop] });

  // A refused change shows its reason in red with Retry, and the switch goes back to where it was saved.
  const refused = await hold(page, (route) => route.fulfill({ status: 409, contentType: "application/json",
    body: JSON.stringify({ error: "Another window changed this setting." }) }));
  await press(weekly);
  await expect(weekly).not.toBeChecked();
  await expect(next).toBeHidden();
  await refused();
  const retry = area.getByRole("button", { name: "Retry", exact: true });
  await walk.state("08-refused-in-red", { visible: [area.getByRole("alert").filter({ hasText: "Not saved: Another window changed this setting." }), retry, next], hidden: [offLine] });
  await expect(weekly).toBeChecked();
  await press(retry);
  await walk.state("09-weekly-off", { visible: [offLine], hidden: [next, area.getByRole("alert"), sheet] });
  await expect(weekly).not.toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("a skipped week, an unavailable run and a change of terms say why", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const press = (target: Locator) => (phone ? target.tap() : target.click());
  const area = page.getByRole("region", { name: "Improvement suggestions", exact: true });
  const now = area.getByRole("button", { name: "Get suggestions now", exact: true });
  const reason = area.getByText(/^Needs 5 finished tasks since \w{3} \d+; 3 so far\.$/);

  await walk.open("/settings/projects/beacon");
  await area.scrollIntoViewIfNeeded();
  await expect(now).toHaveAttribute("aria-disabled", "true");
  await walk.state("10-skipped-week", {
    visible: [area.getByText(/^Skipped \w{3} \d+: 3 tasks had finished\. Next: Monday, \w{3} \d+, if 2 more tasks finish\.$/), now],
    hidden: [area.getByText(/^Reads the/)],
  });
  // The reason is the button's accessible description, shown under it on press.
  await expect(now).toHaveAccessibleDescription(/^Needs 5 finished tasks since \w{3} \d+; 3 so far\.$/);
  await expect(reason).toHaveClass("sr-only");
  await (phone ? now.tap({ force: true }) : now.click({ force: true })); // aria-disabled: the press reveals the reason
  await walk.state("11-unavailable-reason-on-press", { visible: [reason], hidden: [page.getByRole("dialog")] });
  await expect(reason).not.toHaveClass(/sr-only/);

  await walk.open("/projects/cove");
  const chat = page.getByRole("region", { name: "Conversation", exact: true });
  const line = chat.getByText(/^Improvement suggestions are off until you agree to the new terms in/);
  await walk.state("12-terms-changed-in-chat", { visible: [line, line.getByRole("link", { name: "settings", exact: true })], hidden: [] });
  await press(line.getByRole("link", { name: "settings", exact: true }));
  await expect(page).toHaveURL(/\/settings\/projects\/cove#improvement$/);
  const switchOff = area.getByRole("switch", { name: /^Weekly/ });
  await walk.state("13-off-because-the-terms-changed", {
    visible: [area.getByText("Off: the terms changed. Turn on to see them.", { exact: true })], hidden: [],
  });
  await expect(switchOff).not.toBeChecked();
  await expect(area).toBeInViewport();
  await press(switchOff);
  await expect(page.getByRole("dialog", { name: "Turn on improvement suggestions for cove?" })).toBeVisible();
});
