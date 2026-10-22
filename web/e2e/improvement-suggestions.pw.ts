import { expect, type Locator, type Page, type Route } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Improvement suggestions (design/wireframes/improvement-review/PROPOSAL.md, "Where suggestions appear"): through
 * the real run lifecycle, a posted review is one card in the project's chat with Make a task, Dismiss, Undo and
 * Details; endings are short lines with Show; a running review is a line with Stop; cards still waiting for a
 * decision or a task are listed under Suggestions in Needs you, linking back to the card in the chat; settings sum
 * past runs.
 */
test.use({ serviceScript: "improvement-suggestions-service.py" });

/** Holds every press until released, so its saving state can be captured. */
async function hold(page: Page, reply?: (route: Route) => Promise<void>) {
  let release = () => {};
  const released = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/improve", async (route) => {
    await released;
    await (reply ? reply(route) : route.continue());
  });
  return async () => {
    release();
    await page.unrouteAll({ behavior: "wait" });
  };
}

test("posted cards, endings and the running line take their presses and appear under Suggestions", async ({ page }, info) => {
  test.setTimeout(120_000);
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const press = (target: Locator) => (phone ? target.tap() : target.click());
  const chat = page.getByRole("region", { name: "Conversation", exact: true });
  const cards = chat.locator("article.irc");
  const earlier = cards.first();
  const recent = cards.last();
  const reports = earlier.getByRole("region", { name: "1. Owners restate the brief in every report" });
  const crane = earlier.getByRole("region", { name: "2. Crane slots are renamed by hand" });
  const reviewer = earlier.getByRole("region", { name: "3. Reviews wait for a reviewer that is busy" });
  const reask = recent.getByRole("region", { name: "1. Owners re-ask merge questions you already answered" });
  const checks = recent.getByRole("region", { name: "3. Reports name checks that never ran" });
  const failed = chat.getByText("Improvement suggestions failed · it went over its usage limit", { exact: true });
  const nothing = chat.getByText(/^Improvement suggestions · 5 tasks from .+ · nothing to suggest$/);
  const running = chat.locator(".sys-line[data-progress]");
  const stop = running.getByRole("button", { name: "Stop", exact: true });
  const opening = (suggestion: Locator) => suggestion.getByText("Accepted · L3 is opening a task", { exact: true });
  const make = (suggestion: Locator) => suggestion.getByRole("button", { name: "Make a task", exact: true });
  const dismiss = (suggestion: Locator) => suggestion.getByRole("button", { name: "Dismiss", exact: true });
  const saving = (suggestion: Locator) => suggestion.getByRole("status", { name: "Saving" });

  await walk.open("/projects/alpha");
  await expect(cards).toHaveCount(2);
  await walk.state("01-cards-endings-and-running-line", {
    visible: [reports, crane.getByText("Not decided", { exact: true }), reviewer.getByText(/^Accepted · no task yet ·/),
      failed, nothing, reask, checks, make(reask), dismiss(checks), running, stop],
    hidden: [recent.getByRole("region", { name: /^2\. / }), make(crane), dismiss(reviewer), opening(reviewer)],
  });
  await expect(running.locator(".sys-text")).toHaveText(/^Improvement suggestions · reading 5 tasks from [^·]+$/);
  await expect(reports.getByRole("link", { name: /Start reports from the outcome/ })).toBeVisible();
  await expect(reask.locator(".chip")).toHaveText("How agents work");
  await expect(reask.getByText("Your messages after a report in 5 of 5 tasks", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  // Details: what was read, what L3 dropped, the model and its usage, and when it ran.
  await press(recent.getByRole("button", { name: "Details", exact: true }));
  const details = recent.locator(".irc-details");
  await walk.state("02-card-details-open", {
    visible: [details.getByText("L3 dropped", { exact: true }), details.getByText("Suggestion 2: an open task already adds that target", { exact: true }),
      details.getByText("Claude Sonnet 5.5", { exact: true }), details.getByText("900 of its 86,000-token limit", { exact: true }),
      details.getByText("Ran", { exact: true }), recent.getByRole("button", { name: "Hide details", exact: true })],
    hidden: [recent.getByRole("button", { name: "Details", exact: true })],
  });
  await press(recent.getByRole("button", { name: "Hide details", exact: true }));
  await expect(details).toBeHidden();

  // An ending's Show opens the same details; a failure names its reason.
  const ending = chat.locator(".irc-ending").filter({ hasText: /nothing to suggest/ });
  await press(ending.getByRole("button", { name: "Show", exact: true }));
  await walk.state("03-nothing-to-suggest-details", {
    visible: [ending.locator(".irc-details").getByText(/^5 tasks that finished .+; their questions, reviews, landings and conversations/),
      ending.getByRole("button", { name: "Hide", exact: true })],
    hidden: [ending.getByRole("button", { name: "Show", exact: true })],
  });
  await press(ending.getByRole("button", { name: "Hide", exact: true }));
  await expect(ending.locator(".irc-details")).toBeHidden();

  // Dismiss folds the suggestion at the press, its Undo waiting for the save; Undo unfolds it at the press.
  const undo = checks.getByRole("button", { name: "Undo", exact: true });
  const dismissing = await hold(page);
  await press(dismiss(checks));
  await walk.state("04-dismissed-at-the-press", {
    visible: [checks.getByText(/^Dismissed/), saving(checks)],
    hidden: [make(checks), dismiss(checks), undo, checks.getByText("Two reports listed a check as passed without its log.")],
  });
  await dismissing();
  await walk.state("04b-dismissed-with-undo", { visible: [checks.getByText(/^Dismissed/), undo], hidden: [saving(checks), make(checks)] });
  const undoing = await hold(page);
  await press(undo);
  await walk.state("04c-undone-at-the-press", { visible: [make(checks), dismiss(checks), saving(checks)], hidden: [undo, checks.getByText(/^Dismissed/)] });
  await expect(dismiss(checks)).toBeDisabled();
  await undoing();
  await expect(saving(checks)).toBeHidden();
  await expect(dismiss(checks)).toBeEnabled();

  // A refused Dismiss unfolds again, saying why with Retry.
  const failing = await hold(page, (route) => route.fulfill({ status: 500, contentType: "application/json", body: '{"error":"fictional failure"}' }));
  await press(dismiss(checks));
  await expect(checks.getByText(/^Dismissed/)).toBeVisible();
  await failing();
  const retry = checks.getByRole("alert").getByRole("button", { name: "Retry", exact: true });
  await walk.state("05-press-not-saved", {
    visible: [checks.getByRole("alert").filter({ hasText: "Not saved: fictional failure" }), retry, make(checks), dismiss(checks)],
    hidden: [saving(checks), checks.getByText(/^Dismissed/)],
  });
  // Retry dismisses it; Undo brings it back for the presses below.
  await press(retry);
  await expect(undo).toBeVisible();
  await expect(checks.getByRole("alert")).toBeHidden();
  await press(undo);
  await expect(dismiss(checks)).toBeEnabled();

  // Make a task: a spinner while it saves, then L3 is opening its task.
  const saved = await hold(page);
  await press(make(reask));
  await walk.state("06-make-a-task-saving", {
    visible: [saving(reask)],
    hidden: [opening(reask), checks.getByRole("alert")],
  });
  await expect(reask.locator(".irc-make")).toBeDisabled();
  await expect(dismiss(checks)).toBeDisabled();
  await saved();
  await walk.state("07-accepted-l3-opening-a-task", {
    visible: [opening(reask), make(checks), chat.getByText("Task for a suggestion", { exact: true })],
    hidden: [make(reask), dismiss(reask), saving(reask)],
  });

  // An accepted suggestion whose task turn ended without a task: Make a task asks L3 once more.
  await press(reviewer.getByRole("button", { name: "Make a task", exact: true }));
  await walk.state("08-try-again-reopens", {
    visible: [opening(reviewer)],
    hidden: [reviewer.getByText(/^Accepted · no task yet ·/)],
  });

  // Seen in: three cited tasks, then the rest in place; a task opens in place and Back returns.
  const seen = reask.locator(".irc-meta").filter({ hasText: /^Seen in/ });
  await press(seen.getByRole("button", { name: "3 more", exact: true }));
  await expect(seen.getByText("the project chat", { exact: true })).toBeVisible();
  const cited = seen.getByRole("link").first();
  const title = (await cited.textContent())!;
  await expect(cited).toHaveAttribute("href", /^\/projects\/alpha\/tasks\/dock-\d+$/);
  await press(cited);
  await expect(page).toHaveURL(/\/projects\/alpha\/tasks\/dock-\d+$/);
  await walk.state("09-cited-task-opens-in-place", {
    visible: [page.getByRole("heading", { name: title, exact: true }).first()],
    hidden: [reask],
  });
  await page.goBack();
  await expect(page).toHaveURL(/\/projects\/alpha$/);
  await expect(reask).toBeVisible();

  // Needs you lists both cards under Suggestions, newest first, without Details; the header opens the card in chat.
  await walk.open("/");
  const suggestions = page.getByRole("region", { name: "Suggestions", exact: true });
  const listed = suggestions.locator("article.irc");
  await walk.state("10-needs-you-suggestions", {
    visible: [suggestions.getByRole("heading", { name: "Suggestions" }), listed.first(), listed.last(),
      listed.first().getByText("Accepted · L3 is opening a task", { exact: true })],
    hidden: [suggestions.getByRole("button", { name: "Details", exact: true })],
  });
  await expect(listed).toHaveCount(2);
  await expect(listed.last().getByRole("region", { name: "2. Crane slots are renamed by hand" })).toBeVisible();
  await press(listed.last().getByRole("link", { name: "Improvement suggestions", exact: true }));
  await expect(page).toHaveURL(/\/projects\/alpha#improvement-[0-9a-f]{12}$/);
  await walk.state("11-card-opened-from-needs-you", { visible: [earlier, reports], hidden: [suggestions] });
  await expect(earlier).toBeInViewport();

  // Stop: a failed press says so beside the line; a saved one ends the review with its line.
  const stopFails = await hold(page, (route) => route.fulfill({ status: 500, contentType: "application/json", body: '{"error":"fictional failure"}' }));
  await running.scrollIntoViewIfNeeded();
  await press(stop);
  await expect(running.getByRole("status", { name: "Stopping" })).toBeVisible();
  await stopFails();
  await walk.state("12-stop-not-saved", {
    visible: [running.getByText("Not stopped.", { exact: true }), stop],
    hidden: [running.getByRole("status", { name: "Stopping" })],
  });
  await press(stop);
  await walk.state("13-stopped-by-you", {
    visible: [chat.getByText("Improvement suggestions stopped by you", { exact: true })],
    hidden: [running, stop],
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  // The review's page sums the reviews that posted or found nothing; Latest opens the latest card in chat.
  await walk.open("/projects/alpha/agents/weekly");
  const area = page.getByRole("region", { name: "What it does", exact: true });
  const past = area.getByText(/^3 runs · 5 suggestions · 1 became a task · 0 dismissed/);
  await past.scrollIntoViewIfNeeded();
  await walk.state("14-past-suggestions", { visible: [past, past.getByRole("link", { name: "Latest", exact: true })], hidden: [] });
  await press(past.getByRole("link", { name: "Latest", exact: true }));
  await expect(page).toHaveURL(/\/projects\/alpha#improvement-[0-9a-f]{12}$/);
  await walk.state("15-latest-card-from-its-page", { visible: [recent], hidden: [area] });
  await expect(recent).toBeInViewport();
});
