import { expect, type Locator, type Page, type Route } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * The Weekly improvement review as a built-in agent (design/wireframes/project-agents/PROPOSAL.md, "The Weekly
 * improvement review", States 39 to 51): through the real handlers and records, Built in until Turn on agrees to the
 * terms, then the first of the operator's agents with its own page: the status card's every state and refusal, recent
 * runs opening their place in chat, a skipped week, an unavailable Run now, and a change of terms pausing it.
 */

/** Holds every press until released, so its pending state can be captured; release waits for the press to arrive. */
async function hold(page: Page, url = "**/api/improve", reply?: (route: Route) => Promise<void>) {
  let arrive = () => {};
  const arrived = new Promise<void>((resolve) => { arrive = resolve; });
  let release = () => {};
  const released = new Promise<void>((resolve) => { release = resolve; });
  await page.route(url, async (route) => {
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

const refuse = (status: number, error: string) => (route: Route) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify({ error }) });

const fits = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

test.describe(() => {
  test.use({ serviceScript: "weekly-review-service.py" });

  test("Built in until Turn on agrees to the terms, then its page answers every press", async ({ page }, info) => {
    test.setTimeout(120_000);
    const walk = walkthrough(page, info);
    const phone = info.project.name === "phone";
    const press = (target: Locator) => (phone ? target.tap() : target.click());
    const main = page.getByRole("main");
    const builtIn = main.getByRole("region", { name: "Built in", exact: true });
    const builtRow = builtIn.locator(".agents-builtin-row").filter({ hasText: "Weekly improvement review" });
    const turnOnRow = builtRow.getByRole("button", { name: "Turn on", exact: true });
    const sheet = page.getByRole("dialog", { name: "Turn on the Weekly improvement review for altitude?" });
    const agree = sheet.getByRole("button", { name: "Turn on", exact: true });
    const cancel = sheet.getByRole("button", { name: "Cancel", exact: true });
    const yours = main.getByRole("link", { name: /^Weekly improvement review/ });

    await walk.open("/projects/altitude/agents");
    await walk.state("39-built-in", {
      visible: [builtRow, builtRow.getByText("Mondays at 08:00 · Suggests fixes for friction that keeps coming back in this project's tasks", { exact: true }), turnOnRow],
      hidden: [yours, sheet],
    });
    await expect(builtIn.locator(".agents-builtin-row").first()).toContainText("Weekly improvement review");

    // The sheet first: Cancel and Escape change nothing.
    await press(turnOnRow);
    await walk.state("40-terms-sheet", {
      visible: [sheet, sheet.getByText(/reads counts from this project's tasks and short quotes from its chats \(up to 300 characters each\)\./), agree, cancel],
      hidden: [yours],
    });
    await expect(cancel).toBeFocused();
    await expect(page.locator(".overlay")).toHaveAttribute("data-side", phone ? "bottom" : "center");
    await press(cancel);
    await expect(sheet).toBeHidden();
    await press(turnOnRow);
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
    await expect(yours).toBeHidden();

    // A refusal returns the button with why.
    await press(turnOnRow);
    const refused = await hold(page, "**/api/improve", refuse(409, "The terms changed while the sheet was open; read them again."));
    await press(agree);
    await expect(builtRow.getByRole("button", { name: /Turning on Weekly improvement review/ })).toBeDisabled();
    await refused();
    await walk.state("41-turn-on-refused", {
      visible: [builtRow.getByRole("alert").filter({ hasText: "Not turned on: The terms changed while the sheet was open; read them again." }), turnOnRow],
      hidden: [sheet, yours],
    });

    // Turn on spins until the list reads it as yours: the first of your agents.
    await press(turnOnRow);
    const turning = await hold(page);
    await press(agree);
    await walk.state("41-turning-on", { visible: [builtRow.getByRole("button", { name: /Turning on Weekly improvement review/ })], hidden: [sheet] });
    await turning();
    await walk.state("42-yours", { visible: [yours, yours.getByText("Mondays at 08:00 · Never run", { exact: true })], hidden: [builtRow] });
    await expect(main.locator(".agents-row").first()).toContainText("Weekly improvement review");
    await fits(page);

    // Its page: loading, a failed read with Retry, then the status card with Run now's line and no runs yet.
    // Every read fails once released, through the query's own retries, until Retry is pressed.
    let fail = () => {};
    const failing = new Promise<void>((resolve) => { fail = resolve; });
    await page.route("**/api/agents/altitude/weekly", async (route) => { await failing; await refuse(500, "broken")(route); });
    await press(yours);
    await expect(page).toHaveURL(/\/projects\/altitude\/agents\/weekly$/);
    await walk.state("43-loading", { visible: [main.getByRole("status", { name: "Loading Weekly improvement review…" })], hidden: [] });
    fail();
    const failed = main.getByText("Could not load the Weekly improvement review.", { exact: true });
    await expect(failed).toBeVisible({ timeout: 20_000 });
    const retry = main.getByRole("button", { name: "Retry", exact: true });
    await walk.state("43-failed", { visible: [failed, retry], hidden: [] });
    await page.unrouteAll({ behavior: "wait" });
    await press(retry);
    const card = main.locator(".agent-status");
    const next = card.getByText(/^Next: Monday, \w{3} \d+$/);
    const runNow = card.getByRole("button", { name: "Run now", exact: true });
    const pause = card.getByRole("button", { name: "Pause", exact: true });
    const resume = card.getByRole("button", { name: "Resume", exact: true });
    const stop = card.getByRole("button", { name: "Stop", exact: true });
    const reads = card.getByText(/^Reads the 6 tasks finished since \w{3} \d+\.$/);
    await walk.state("45-ready-no-runs", {
      visible: [next, runNow, pause, reads, main.getByText("No runs yet.", { exact: true }),
        main.getByText("Finds friction that keeps coming back in this project's tasks and suggests fixes, as cards in chat you can make into tasks.", { exact: true }),
        main.getByText(/^Counts and short chat quotes · .+ on your plan · capped per run, plus one L3 check$/)],
      hidden: [stop, resume, main.getByRole("button", { name: /Edit|Retire/ })],
    });
    await fits(page);

    // Run now refused, then Starting… with Pause unavailable, then its running line with Stop only.
    const runRefused = await hold(page, "**/api/improve", refuse(409, "Another review just ended; ask again."));
    await press(runNow);
    await runRefused();
    await walk.state("46-run-refused", { visible: [card.getByRole("alert").filter({ hasText: /^Another review just ended; ask again\.$/ }), runNow, pause], hidden: [stop] });
    const starting = await hold(page);
    await press(runNow);
    await walk.state("45-starting", { visible: [card.getByRole("button", { name: "Starting…", exact: true })], hidden: [card.getByRole("alert")] });
    await expect(pause).toBeDisabled();
    await starting();
    const reading = card.getByText("Reading 6 tasks", { exact: true });
    await walk.state("47-running", { visible: [reading, stop], hidden: [runNow, pause, reads] });

    // Stop failed, then Stopping… until the end is read: the stopped run leads Recent runs.
    const stopFailed = await hold(page, "**/api/improve", refuse(500, "broken"));
    await press(stop);
    await stopFailed();
    await walk.state("47-stop-failed", { visible: [card.getByRole("alert").filter({ hasText: "Not stopped. Try again." }), stop, reading], hidden: [] });
    const stopping = await hold(page);
    await press(stop);
    await walk.state("47-stopping", { visible: [card.getByRole("button", { name: "Stopping…", exact: true }), reading], hidden: [] });
    await stopping();
    const stopped = main.getByRole("link", { name: /^Stopped by you/ });
    await walk.state("47-stopped", { visible: [stopped, stopped.getByText("It was reading 6 tasks.", { exact: true }), runNow, pause], hidden: [reading, stop] });
    await expect(stopped).toHaveAttribute("href", /^\/projects\/altitude#improvement-[0-9a-f]{12}$/);

    // Pause refused with Retry, then Paused with Resume and Run now; Resume returns to when it runs next.
    const pauseRefused = await hold(page, "**/api/improve", refuse(409, "Another window changed it."));
    await press(pause);
    await expect(card.getByText("Paused", { exact: true })).toBeVisible();
    await pauseRefused();
    const again = card.getByRole("button", { name: "Retry", exact: true });
    await walk.state("46-pause-refused", { visible: [card.getByRole("alert").filter({ hasText: "Not saved: Another window changed it." }), again, next, pause], hidden: [resume] });
    await press(again);
    await walk.state("48-paused", { visible: [card.getByText("Paused", { exact: true }), resume, runNow], hidden: [pause, next, card.getByRole("alert")] });
    await press(resume);
    await expect(next).toBeVisible();
    await expect(pause).toBeEnabled();

    // Back names the project's Agents page, where the row now says it was stopped.
    await press(main.locator("a.agents-back").or(page.locator("header a.agents-back")).first());
    await expect(page).toHaveURL(/\/projects\/altitude\/agents$/);
    await expect(yours.getByText(/^Mondays at 08:00 · Stopped by you /)).toBeVisible();
  });

  test("too few tasks: a skipped week and why Run now cannot run; a change of terms pauses it", async ({ page }, info) => {
    const walk = walkthrough(page, info);
    const phone = info.project.name === "phone";
    const press = (target: Locator) => (phone ? target.tap() : target.click());
    const main = page.getByRole("main");
    const card = main.locator(".agent-status");
    const runNow = card.getByRole("button", { name: "Run now", exact: true });
    const reason = card.getByText(/^Run now needs 5 finished tasks since \w{3} \d+; 3 so far\.$/);

    await walk.open("/projects/beacon/agents/weekly");
    const skipped = main.locator(".agent-run").filter({ hasText: "Skipped" });
    await walk.state("44-too-few", {
      visible: [card.getByText(/^Next: Monday, \w{3} \d+, if 2 more tasks finish$/), runNow, skipped,
        skipped.getByText("3 tasks had finished; it needs 5.", { exact: true })],
      hidden: [card.getByText(/^Reads the/), skipped.locator(".agents-chevron")],
    });
    await expect(skipped).not.toHaveAttribute("href");
    await expect(runNow).toHaveAttribute("aria-disabled", "true");
    await expect(runNow).toHaveAccessibleDescription(/^Run now needs 5 finished tasks since \w{3} \d+; 3 so far\.$/);
    await expect(reason).toHaveClass("sr-only");
    // aria-disabled: still pressable, so the press reveals the reason.
    await (phone ? runNow.tap({ force: true }) : runNow.click({ force: true }));
    await walk.state("44-reason-on-press", { visible: [reason], hidden: [page.getByRole("dialog")] });

    await walk.open("/projects/cove");
    const chat = page.getByRole("region", { name: "Conversation", exact: true });
    const line = chat.locator(".sys-line").filter({ hasText: "paused: its terms changed." });
    const open = line.getByRole("link", { name: "Open", exact: true });
    await walk.state("51-chat-line", { visible: [line, line.getByText("Weekly improvement review", { exact: true }), open], hidden: [] });
    await press(open);
    await expect(page).toHaveURL(/\/projects\/cove\/agents\/weekly$/);
    const resume = card.getByRole("button", { name: "Resume", exact: true });
    await walk.state("48-terms-changed", { visible: [card.getByText("Paused: its terms changed", { exact: true }), resume, runNow], hidden: [] });
    await press(resume);
    await expect(page.getByRole("dialog", { name: "Turn on the Weekly improvement review for cove?" })).toBeVisible();
  });
});

test.describe(() => {
  test.use({ serviceScript: "improvement-suggestions-service.py" });

  test("every kind of run, newest first, each opening its place in chat, and what it does with its totals", async ({ page }, info) => {
    const walk = walkthrough(page, info);
    const phone = info.project.name === "phone";
    const press = (target: Locator) => (phone ? target.tap() : target.click());
    const main = page.getByRole("main");
    const runs = main.locator(".agent-run");
    const latest = main.getByRole("link", { name: "Latest", exact: true });

    await walk.open("/projects/alpha/agents/weekly");
    await walk.state("49-every-run", {
      visible: [main.locator(".agent-status").getByText("Reading 5 tasks", { exact: true }),
        runs.filter({ hasText: "2 suggestions" }).getByText("Owners re-ask merge questions you already answered; Reports name checks that never ran", { exact: true }),
        runs.filter({ hasText: "Nothing to suggest" }).getByText("5 tasks read.", { exact: true }),
        runs.filter({ hasText: "Couldn't finish" }).getByText(/^It went over its usage limit/),
        runs.filter({ hasText: "3 suggestions" }), latest,
        main.getByText(/^3 runs · 5 suggestions · 1 became a task · 0 dismissed/)],
      hidden: [],
    });
    expect(await runs.locator("strong").allTextContents()).toEqual(["2 suggestions", "Nothing to suggest", "Couldn't finish", "3 suggestions"]);
    await fits(page);
    await press(runs.filter({ hasText: "Nothing to suggest" }));
    await expect(page).toHaveURL(/\/projects\/alpha#improvement-[0-9a-f]{12}$/);
    await expect(page.locator(".irc-ending").filter({ hasText: "nothing to suggest" })).toBeInViewport();
  });
});
