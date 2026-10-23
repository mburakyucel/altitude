import { expect, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Project agents (design/wireframes/project-agents/PROPOSAL.md, "Screens" and "States") through the real handlers and
 * records of project-agents-service.py: Work's run rows and Agents row, the Agents page, one agent's status card and
 * runs, a run's page, the form, Retire, a retired agent, and the chat's result lines and L3's offer card.
 */
test.use({ serviceScript: "project-agents-service.py" });

const project = "/projects/harbor";

/** Holds the next agent press until released, so its in-between state can be captured. */
async function hold(page: Page) {
  let arrive = () => {};
  const arrived = new Promise<void>((resolve) => { arrive = resolve; });
  let release = () => {};
  const released = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/agents", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    arrive();
    await released;
    await route.continue();
  });
  return async () => {
    await arrived;
    release();
    await page.unrouteAll({ behavior: "wait" });
  };
}

function setup(page: Page, phone: boolean) {
  const press = (target: Locator, options: { force?: boolean } = {}) => (phone ? target.tap(options) : target.click(options));
  const fits = async () => {
    if (phone) expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no horizontal overflow").toBe(true);
    // A card that hides its overflow shows all of its rows (the budget's select once fell out of a shrunk card).
    expect(await page.locator(".agents-page .settings-group").evaluateAll((groups) =>
      groups.filter((group) => group.scrollHeight > group.clientHeight + 1).length), "clipped cards").toBe(0);
  };
  /** No line on an agents page says again what another line already says. */
  const noRepeats = async () => {
    const lines = await page.locator(".agents-page").evaluate((root: HTMLElement) => {
      // A control's own label (each built-in's Turn on) is an action, not a line.
      const controls = new Set([...root.querySelectorAll<HTMLElement>("button, select")].map((control) => control.innerText.trim()));
      return root.innerText.split("\n").map((line) => line.trim()).filter((line) => line.length > 3 && !controls.has(line));
    });
    expect(lines.filter((line, index) => lines.indexOf(line) !== index), "repeated lines").toEqual([]);
  };
  const title = (name: string | RegExp) => page.getByRole("heading", { level: 1, name });
  const back = page.locator(".agents-back");
  /** On phone the line under the title keeps its space below the header. */
  const clearOfHeader = async (line: Locator) => {
    if (!phone) return;
    await page.locator(".agents-page").evaluate((scroller) => scroller.scrollTo(0, 0));
    const header = await page.locator(".phone-header").boundingBox();
    const box = await line.boundingBox();
    expect(box!.y - (header!.y + header!.height), "space under the phone header").toBeGreaterThanOrEqual(8);
  };
  return { press, fits, noRepeats, title, back, clearOfHeader };
}

/** The agent page an Agents row opens. */
async function openAgent(page: Page, press: (target: Locator) => Promise<unknown>, name: string) {
  await page.goto(`${project}/agents`);
  await press(page.getByRole("link", { name: new RegExp(`^${name}`) }));
  await expect(page.getByRole("heading", { level: 1, name, exact: true })).toBeVisible();
}

test("Work shows agent runs and the Agents row; the Agents page lists, budgets and turns on built-ins", async ({ page }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { press, fits, noRepeats, title } = setup(page, phone);
  const work = page.getByRole("region", { name: "Work", exact: true });
  const running = work.getByRole("link", { name: /^Flaky test watch · Routine run · \d+ of 20 min$/ });
  const waiting = work.getByRole("link", { name: "Dependency check · Routine run · waiting while Flaky test watch runs", exact: true });
  const agentsRow = work.getByRole("link", { name: "Routines · 4", exact: true });

  await walk.open(phone ? `${project}?tab=work` : project);
  await walk.state("01-work-agent-runs", {
    visible: [running, waiting, agentsRow, work.getByText("2 current · 0 done this week", { exact: true }),
      work.getByRole("heading", { name: "Current (2)" })],
    hidden: [work.getByText("No current tasks. Ask L3 to start something.", { exact: true }), work.getByRole("link", { name: /Old lint sweep/ })],
  });
  await fits();

  await press(agentsRow);
  await expect(page).toHaveURL(/\/projects\/harbor\/agents$/);
  const rows = {
    dependency: page.getByRole("link", { name: /^Dependency check/ }),
    notes: page.getByRole("link", { name: /^Release notes check/ }),
    paused: page.getByRole("link", { name: /^Test timing/ }),
    flaky: page.getByRole("link", { name: /^Flaky test watch/ }),
  };
  const retiredLink = page.getByRole("button", { name: "1 retired", exact: true });
  const retiredHeading = page.getByRole("heading", { name: "Retired routines" });
  const oldLint = page.getByRole("link", { name: /^Old lint sweep/ });
  const builtIn = page.getByRole("region", { name: "Built in" });
  const budget = page.getByLabel("Routines may use up to");
  await walk.state("02-agents-page", {
    visible: [title("Routines"), page.getByText("Give an agent a job and schedule it or make it event-driven.", { exact: true }),
      ...Object.values(rows),
      page.getByText("Weekdays at 07:00 · Waiting while Flaky test watch runs", { exact: true }),
      page.getByText(/^Weekdays at 07:00 · Couldn't finish \S/),
      page.getByText("Weekdays at 07:00 · Paused", { exact: true }),
      page.getByText(/^Only when you run it · Running, \d+ of 20 min$/),
      budget, page.getByText("20 min used this week", { exact: true }), page.getByRole("link", { name: "New routine", exact: true }),
      retiredLink, builtIn.getByText("Build health", { exact: true }),
      builtIn.getByText("Mondays at 08:00 · Runs the project's checks on a clean copy and says what broke", { exact: true }),
      builtIn.getByText("What landed", { exact: true })],
    hidden: [retiredHeading, oldLint, builtIn.getByText("Dependency check")],
  });
  await expect(budget).toHaveValue("120");
  await expect(builtIn.getByRole("button", { name: "Turn on", exact: true })).toHaveCount(3);
  await fits();
  await noRepeats();

  // The budget shows its choice at once with Saving…, then the saved value.
  const budgetSaved = await hold(page);
  await budget.selectOption({ label: "5 h a week" });
  await walk.state("03-budget-saving", { visible: [page.getByRole("status").filter({ hasText: "Saving…" })], hidden: [page.getByText("20 min used this week", { exact: true })] });
  await expect(budget).toHaveValue("300");
  await budgetSaved();
  await walk.state("04-budget-saved", { visible: [page.getByText("20 min used this week", { exact: true })], hidden: [page.getByText("Saving…"), page.getByRole("alert")] });
  await expect(budget).toHaveValue("300");

  await press(retiredLink);
  await walk.state("05-retired-open", {
    visible: [retiredHeading, oldLint, page.getByText(/^Retired \w{3} \d+ · 0 runs$/)], hidden: [],
  });
  await expect(retiredLink).toHaveAttribute("aria-expanded", "true");
  await press(retiredLink);
  await walk.state("06-retired-closed", { visible: [retiredLink], hidden: [retiredHeading, oldLint] });

  // Turn on spins until the agent reads as on; its row leaves Built in and joins the agents.
  const buildHealth = builtIn.locator(".agents-builtin-row").filter({ hasText: "Build health" });
  const turnedOn = await hold(page);
  await press(buildHealth.getByRole("button", { name: "Turn on", exact: true }));
  await walk.state("07-turning-on", { visible: [buildHealth.getByText("Turning on Build health")], hidden: [] });
  await expect(builtIn.getByRole("button", { name: "Turn on", exact: true }).last()).toBeDisabled();
  await turnedOn();
  await walk.state("08-built-in-turned-on", {
    visible: [page.getByRole("link", { name: /^Build health/ }), page.getByText("Mondays at 08:00 · Never run", { exact: true }),
      builtIn.getByText("What landed", { exact: true })],
    hidden: [builtIn.getByText("Build health"), page.getByText("Turning on Build health")],
  });
  await fits();

  // The project menu opens the same page.
  await walk.open(project);
  await press(page.getByRole("button", { name: "More actions" }));
  const menuItem = page.getByRole("menu", { name: "Project actions" }).getByRole("menuitem", { name: "Routines…", exact: true });
  await walk.state("09-menu-agents", { visible: [menuItem], hidden: [] });
  await press(menuItem);
  await walk.state("10-agents-from-menu", { visible: [title("Routines"), rows.dependency], hidden: [page.getByRole("menu")] });
  await expect(page).toHaveURL(/\/projects\/harbor\/agents$/);
});

test("an agent's status card answers Stop, Pause, Resume and Run now; its runs and brief", async ({ page }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { press, fits, noRepeats } = setup(page, phone);
  const status = page.locator(".agent-status");
  const button = (name: string) => status.getByRole("button", { name, exact: true });

  // A waiting run: Stop only, Stopping… until it reads as ended, then the agent's next time with Run now and Pause.
  await openAgent(page, press, "Dependency check");
  await walk.state("11-waiting", {
    visible: [status.getByText("Waiting while Flaky test watch runs", { exact: true }), button("Stop"),
      page.getByRole("link", { name: /^Waiting/ })],
    hidden: [button("Run now"), button("Pause"), status.getByRole("link", { name: "Open" })],
  });
  const stopped = await hold(page);
  await press(button("Stop"));
  await walk.state("12-stopping", { visible: [button("Stopping…")], hidden: [button("Stop")] });
  await stopped();
  await walk.state("13-stopped-waiting-run", {
    visible: [status.getByText(/^Next: \w+ at 07:00$/), button("Run now"), button("Pause"), page.getByRole("link", { name: /^Stopped by you/ })],
    hidden: [button("Stop"), button("Stopping…"), page.getByRole("link", { name: /^Waiting/ })],
  });

  // The brief is clamped to three lines with Show all.
  const showAll = page.getByRole("button", { name: "Show all", exact: true });
  const briefBox = page.locator(".agent-what .agent-brief");
  await walk.state("14-brief-clamped", { visible: [showAll, briefBox, page.getByText(/^Weekdays at 07:00 · Tells you when it finds something · /)], hidden: [] });
  await expect(briefBox).toHaveAttribute("data-clamped", "");
  expect(await briefBox.evaluate((box) => box.scrollHeight > box.clientHeight + 1), "the brief is longer than three lines").toBe(true);
  await press(showAll);
  await walk.state("15-brief-shown", { visible: [page.getByText(/If every dependency is current, say so in one line\.$/)], hidden: [showAll] });
  await expect(briefBox).not.toHaveAttribute("data-clamped");
  await noRepeats();
  await fits();

  // A running run: Open, Stop and Pause. Pause shows at once; Stop ends the run; Resume; Run now starts another.
  await openAgent(page, press, "Flaky test watch");
  await walk.state("16-running", {
    visible: [status.getByText(/^Running · \d+ of 20 min ·/), status.getByRole("link", { name: "Open", exact: true }), button("Stop"), button("Pause")],
    hidden: [button("Run now"), button("Resume")],
  });
  await press(button("Pause"));
  await walk.state("17-paused-while-running", {
    visible: [status.getByText(/^Running · \d+ of 20 min ·/), button("Stop")], hidden: [button("Pause"), button("Resume")],
  });
  await press(button("Stop"));
  await walk.state("18-paused-after-stop", {
    visible: [status.getByText("Paused", { exact: true }), button("Resume"), button("Run now"), page.getByRole("link", { name: /^Stopped by you/ })],
    hidden: [button("Stop"), status.getByRole("link", { name: "Open" })],
  });
  await press(button("Resume"));
  await walk.state("19-resumed", {
    visible: [status.getByText("Only when you run it", { exact: true }), button("Run now"), button("Pause")], hidden: [button("Resume")],
  });
  const started = await hold(page);
  await press(button("Run now"));
  await walk.state("20-starting", { visible: [button("Starting…")], hidden: [button("Run now")] });
  await started();
  await walk.state("21-running-again", {
    visible: [status.getByText(/^Running · 0 of 20 min ·/), button("Stop"), page.getByRole("link", { name: /^Running/ })],
    hidden: [button("Starting…"), button("Run now")],
  });
  await fits();

  // Runs newest first: the skipped run says why, with no page to open.
  await openAgent(page, press, "Release notes check");
  const skipped = page.locator(".agent-run").filter({ hasText: "Skipped" });
  await walk.state("22-recent-runs", {
    visible: [page.getByRole("link", { name: /^Couldn't finish.*3 min · 413k tokens.*The checkout has no CHANGELOG\.md$/ }),
      skipped.getByText("Your Claude plan is close to its weekly limit", { exact: true }),
      page.getByRole("link", { name: /^Nothing found.*4 min · 422k tokens.*Every merged change has a note\.$/ }),
      page.getByRole("link", { name: /^Found something.*6 min · 440k tokens.*The changelog misses two merged fixes\.$/ })],
    hidden: [page.getByRole("link", { name: /^Skipped/ }), page.getByRole("button", { name: "Show older" }), page.getByText("No runs yet.")],
  });
  await expect(page.locator(".agent-run strong")).toHaveText(["Couldn't finish", "Skipped", "Nothing found", "Found something"]);
  await expect(skipped.getByRole("link")).toHaveCount(0);
  await noRepeats();
  await fits();
});

test("a run's page shows its report, the brief it ran and its session, or why it couldn't finish", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { press, fits, noRepeats, title, back, clearOfHeader } = setup(page, phone);
  await openAgent(page, press, "Release notes check");
  await press(page.getByRole("link", { name: /^Found something/ }));
  const brief = page.getByText(/^Read the release notes in CHANGELOG\.md/);
  const transcript = page.getByText("Compared the changelog with the merged pull requests.");
  await walk.state("23-run-found", {
    visible: [title(/^Run · /), page.getByText("Requested by you · Claude Opus 5.5 · 6 min · 440k tokens", { exact: true }),
      page.locator(".agent-report").getByText("Found something", { exact: true }),
      page.getByText("Two fixes merged this week have no changelog entry:"), page.getByText("Tide timer rounding"),
      page.getByText("Brief it ran", { exact: true }), page.getByText("Session", { exact: true })],
    hidden: [brief, transcript, page.getByRole("button", { name: "Stop" })],
  });
  await clearOfHeader(page.locator(".agent-run-meta"));
  if (phone) {
    // A long agent name in the back link shortens on one line and leaves the title its room.
    const [link, heading] = await Promise.all([back.boundingBox(), title(/^Run · /).boundingBox()]);
    expect(link!.height, "the back link stays on one line").toBeLessThanOrEqual(48);
    expect(await title(/^Run · /).evaluate((h) => h.scrollWidth <= h.clientWidth), "the title is not cut off").toBe(true);
    expect(heading!.x).toBeGreaterThanOrEqual(link!.x + link!.width);
  }
  await press(page.getByText("Brief it ran", { exact: true }));
  await walk.state("24-brief-it-ran", { visible: [brief], hidden: [transcript] });
  await noRepeats();
  // The session opens on its prompt, which carries the brief, then the run's own activity.
  await press(page.getByText("Session", { exact: true }));
  await walk.state("25-session-open", { visible: [transcript], hidden: [] });
  await fits();

  await press(back);
  await press(page.getByRole("link", { name: /^Couldn't finish/ }));
  await walk.state("26-run-couldnt-finish", {
    visible: [page.locator(".agent-report").getByText("Couldn't finish", { exact: true }),
      page.locator(".agent-report").getByText("The checkout has no CHANGELOG.md", { exact: true }),
      page.getByText("Requested by you · Claude Opus 5.5 · 3 min · 413k tokens", { exact: true })],
    hidden: [page.getByText("Two fixes merged this week"), brief],
  });
  await fits();

  // A running run: its elapsed minutes and Stop, with its live session.
  await walk.open(phone ? `${project}?tab=work` : project);
  await press(page.getByRole("region", { name: "Work", exact: true }).getByRole("link", { name: /^Flaky test watch · Routine run/ }));
  await walk.state("27-run-running", {
    visible: [page.getByText(/^Running · [1-9]\d* of 20 min$/), page.getByRole("button", { name: "Stop", exact: true }),
      page.getByText("Requested by you · Claude Opus 5.5", { exact: true })],
    hidden: [page.getByText("Brief it ran"), page.locator(".agent-report")],
  });
  await fits();

  // A waiting run: why it waits and Stop; no model or session before its worker starts.
  await walk.open(phone ? `${project}?tab=work` : project);
  await press(page.getByRole("region", { name: "Work", exact: true }).getByRole("link", { name: /^Dependency check · Routine run/ }));
  await walk.state("28-run-waiting", {
    visible: [page.getByText("Waiting while Flaky test watch runs", { exact: true }), page.getByRole("button", { name: "Stop", exact: true }),
      page.locator(".agent-run-meta").getByText("Requested by you", { exact: true })],
    hidden: [page.getByText("Brief it ran"), page.locator(".agent-report")],
  });
  await fits();
});

test("the form: defaults, what is missing, create, edit and discard", async ({ page }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { press, fits, title, back } = setup(page, phone);
  const name = page.getByLabel("Name", { exact: true });
  const brief = page.getByLabel("What it does", { exact: true });
  const day = (label: string) => page.getByRole("button", { name: label, exact: true });
  const create = page.getByRole("button", { name: "Create routine", exact: true });
  const noName = page.getByText("Give it a name.", { exact: true });
  const noBrief = page.getByText("Say what it does.", { exact: true });
  const startsOff = page.getByText("It starts off: try it with Run now, then turn it on.", { exact: true });
  const noDay = page.getByText("Pick at least one day.", { exact: true });

  await walk.open(`${project}/agents`);
  await press(page.getByRole("link", { name: "New routine", exact: true }));
  await walk.state("29-new-agent-defaults", {
    visible: [title("New routine"), name, brief, page.getByText("On a schedule", { exact: true }), page.getByLabel("Time"),
      page.getByRole("radio", { name: "When it finds something" }), page.getByLabel("Each run stops after"), create,
      page.getByRole("button", { name: "Cancel", exact: true }), page.getByRole("button", { name: "+ Add a trigger", exact: true }), startsOff],
    hidden: [noName, noBrief, noDay, page.getByText("Only when you run it")],
  });
  for (const label of ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]) await expect(day(label)).toHaveAttribute("aria-pressed", "true");
  for (const label of ["Saturday", "Sunday"]) await expect(day(label)).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByLabel("Time")).toHaveValue("07:00");
  await expect(page.getByRole("radio", { name: "When it finds something" })).toBeChecked();
  await expect(page.getByLabel("Each run stops after")).toHaveValue("20");
  await expect(create).toHaveAttribute("aria-disabled", "true");
  // The seven days share one line, 40px each on phone.
  const days = await page.locator(".agent-day").evaluateAll((all) => all.map((button) => button.getBoundingClientRect()));
  expect(new Set(days.map((box) => Math.round(box.top))).size, "days on one line").toBe(1);
  if (phone) expect(days.map((box) => Math.round(box.width))).toEqual(Array(7).fill(40));
  await fits();

  await press(create, { force: true }); // aria-disabled: the press says what is missing
  await walk.state("30-incomplete", { visible: [noName, noBrief], hidden: [noDay] });
  await expect(name).toBeFocused();
  await expect(name).toHaveAccessibleDescription("Give it a name.");
  await name.fill("Docs spelling check");
  await walk.state("31-typing-clears-it", { visible: [create, noBrief], hidden: [noName] });
  await brief.fill("List misspelled words in docs/ and the file each is in.");
  await expect(noBrief).toBeHidden();
  for (const label of ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]) await press(day(label));
  await walk.state("32-no-day", { visible: [noDay], hidden: [noName, noBrief] });
  await expect(create).toHaveAttribute("aria-disabled", "true");
  await press(create, { force: true });
  await expect(day("Monday")).toBeFocused();
  await press(day("Monday"));
  await press(day("Wednesday"));
  await walk.state("33-days-picked", { visible: [create], hidden: [noDay] });
  await expect(create).not.toHaveAttribute("aria-disabled");
  await fits();

  await press(create);
  const status = page.locator(".agent-status");
  await walk.state("34-created-not-on-yet", {
    visible: [title("Docs spelling check"), status.getByText("Not on yet", { exact: true }),
      status.getByRole("button", { name: "Run now", exact: true }), status.getByRole("button", { name: "Turn on", exact: true }),
      page.getByText("No runs yet.", { exact: true }), page.getByText(/^Mon, Wed at 07:00 · Tells you when it finds something · /)],
    hidden: [create, status.getByRole("button", { name: "Pause" })],
  });
  await expect(page).toHaveURL(/\/projects\/harbor\/agents\/[0-9a-f]{12}$/);

  // Edit, then leave: the discard question keeps the edit first.
  await press(page.getByRole("link", { name: "Edit", exact: true }));
  await expect(title("Edit")).toBeVisible();
  await expect(name).toHaveValue("Docs spelling check");
  await name.fill("Docs spelling sweep");
  const dialog = page.getByRole("dialog", { name: "Discard your changes?" });
  await press(page.getByRole("button", { name: "Cancel", exact: true }));
  await walk.state("35-discard-question", {
    visible: [dialog, dialog.getByText("Your edits to Docs spelling check haven’t been saved.", { exact: true }),
      dialog.getByRole("button", { name: "Keep editing" }), dialog.getByRole("button", { name: "Discard" })],
    hidden: [],
  });
  await expect(dialog.getByRole("button", { name: "Keep editing" })).toBeFocused();
  await press(dialog.getByRole("button", { name: "Keep editing" }));
  await walk.state("36-kept-editing", { visible: [title("Edit"), name], hidden: [dialog] });
  await expect(name).toHaveValue("Docs spelling sweep");
  await press(back);
  await press(dialog.getByRole("button", { name: "Discard" }));
  await walk.state("37-discarded", { visible: [title("Docs spelling check"), status.getByText("Not on yet", { exact: true })], hidden: [dialog] });

  // Turn on answers at once.
  await press(status.getByRole("button", { name: "Turn on", exact: true }));
  await walk.state("38-turned-on", {
    visible: [status.getByText(/^Next: \w+ at 07:00$/), status.getByRole("button", { name: "Pause", exact: true })],
    hidden: [status.getByText("Not on yet"), status.getByRole("button", { name: "Turn on" })],
  });
  await fits();
});

test("Retire asks first; a retired agent's page is read only", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { press, fits, noRepeats, title, back, clearOfHeader } = setup(page, phone);
  await openAgent(page, press, "Release notes check");
  const retire = page.getByRole("button", { name: "Retire…", exact: true });
  const dialog = page.getByRole("dialog", { name: "Retire Release notes check?" });
  await press(retire);
  await walk.state("39-retire-question", {
    visible: [dialog, dialog.getByText("It won’t run again, from its triggers or Run now. Its past runs stay readable under Retired.", { exact: true }),
      dialog.getByRole("button", { name: "Cancel" }), dialog.getByRole("button", { name: "Retire", exact: true })],
    hidden: [],
  });
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await press(dialog.getByRole("button", { name: "Cancel" }));
  await walk.state("40-retire-cancelled", { visible: [retire, page.locator(".agent-status")], hidden: [dialog] });
  await press(retire);
  await press(dialog.getByRole("button", { name: "Retire", exact: true }));
  await walk.state("41-retired", {
    visible: [title("Release notes check"), page.getByText(/^Retired \w{3} \d+$/), page.getByRole("link", { name: /^Found something/ }),
      page.getByText(/^Weekdays at 07:00 · Tells you every run · /)],
    hidden: [dialog, page.locator(".agent-status"), page.getByRole("link", { name: "Edit", exact: true }), retire,
      page.getByRole("heading", { name: "Retire routine" })],
  });
  await clearOfHeader(page.locator(".agent-retired-line"));
  await noRepeats();
  await fits();

  await press(back);
  await press(page.getByRole("button", { name: "2 retired", exact: true }));
  await press(page.getByRole("link", { name: /^Old lint sweep/ }));
  await walk.state("42-retired-page", {
    visible: [title("Old lint sweep"), page.getByText(/^Retired \w{3} \d+$/), page.getByText("No runs yet.", { exact: true })],
    hidden: [page.locator(".agent-status"), page.locator(".agents-page").getByRole("button"), page.getByRole("link", { name: "Edit", exact: true })],
  });
  await fits();
});

test("chat: a run's result line opens its run, and L3's offer card creates the agent", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { press, fits, title } = setup(page, phone);
  const chat = page.getByRole("region", { name: "Conversation", exact: true });
  const found = chat.locator(".agent-line").filter({ hasText: "Release notes check found something: The changelog misses two merged fixes." });
  const offer = chat.locator(".agent-offer");
  const createAgent = offer.getByRole("button", { name: "Create routine", exact: true });

  await walk.open(project);
  await walk.state("43-chat-lines-and-offer", {
    visible: [found, found.getByRole("link", { name: "Open", exact: true }),
      chat.locator(".agent-line").filter({ hasText: "Release notes check found nothing." }),
      chat.locator(".agent-line").filter({ hasText: "Release notes check couldn't finish: The checkout has no CHANGELOG.md." }),
      chat.getByText("A routine can do that. Here it is; create it if it reads right."),
      offer.getByText("New routine", { exact: true }), offer.getByText("Docs link check", { exact: true }),
      offer.getByText("Fridays at 16:00 · Tells you when it finds something", { exact: true }),
      offer.getByText("Open every link in docs/ and list the ones that fail."),
      createAgent, offer.getByRole("link", { name: "Edit first", exact: true })],
    hidden: [offer.getByText("Created"), offer.getByRole("alert")],
  });
  await fits();

  await press(createAgent);
  await walk.state("44-offer-created", {
    visible: [offer.getByText(/^Created · Open$/), offer.getByRole("link", { name: "Open", exact: true })],
    hidden: [createAgent, offer.getByRole("link", { name: "Edit first" })],
  });
  await press(offer.getByRole("link", { name: "Open", exact: true }));
  await walk.state("45-offered-agent", {
    visible: [title("Docs link check"), page.locator(".agent-status").getByText("Not on yet", { exact: true })], hidden: [],
  });

  await walk.open(project);
  await press(found.getByRole("link", { name: "Open", exact: true }));
  await walk.state("46-result-line-opens-run", {
    visible: [title(/^Run · /), page.getByText("Two fixes merged this week have no changelog entry:")], hidden: [],
  });
  await fits();
});

test("Monitor lists the running agent among Sessions and every listed agent under Agents", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { press } = setup(page, phone);
  const sessionRow = page.locator(".monitor-session").filter({ hasText: "harbor / Flaky test watch" });
  const meta = sessionRow.getByText(/^\S.* · context 6% · \d+ of 20 min$/);
  const agents = page.getByRole("heading", { name: "Routines (3)", exact: true });
  const section = page.locator("section").filter({ has: agents });
  const row = (name: string) => section.getByRole("link", { name: new RegExp(`^harbor / ${name}`) });
  const fits = async () => {
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no horizontal overflow").toBe(true);
  };
  /** The page scrolls inside the shell: bring Sessions to the top so each capture shows both sections. */
  const toSessions = () => page.getByRole("heading", { name: /^Sessions \(\d+\)$/ }).evaluate((el) => el.scrollIntoView({ block: "start" }));

  await walk.open("/monitor");
  await walk.state("01-monitor-agents", {
    action: toSessions,
    visible: [sessionRow.getByText("Routine", { exact: true }), meta, sessionRow.locator(".monitor-meter"), agents,
      row("Dependency check").getByText("Waiting", { exact: true }),
      row("Release notes check").getByText(/^Next (today|tomorrow|Monday|Tuesday|Wednesday|Thursday|Friday) at 07:00$/),
      row("Release notes check").getByText(/^Last: Couldn't finish · (yesterday|\d+ d ago) · 3 min · \S+ tokens$/),
      row("Flaky test watch").getByText("Running", { exact: true })],
    // Paused by you with no runs this week, and retired: not listed. A first run under way needs no "No runs yet."
    hidden: [row("Flaky test watch").getByText("No runs yet.", { exact: true }), row("Dependency check").getByText("No runs yet."), row("Test timing"), row("Old lint sweep"), sessionRow.getByRole("link"), sessionRow.getByText("Stale", { exact: true })],
  });
  await expect(row("Flaky test watch").locator(".dot[data-pulse]")).toHaveCount(1);
  // Agents come last on the page.
  await expect(page.getByRole("heading", { level: 2 }).last()).toHaveText("Routines (3)");
  // Desktop keeps what comes next on the title's line; the phone stacks it under the title, every row alike.
  for (const name of ["Dependency check", "Release notes check", "Flaky test watch"]) {
    const [title, next] = await Promise.all([row(name).locator("strong").boundingBox(), row(name).locator(".monitor-agent-next").boundingBox()]);
    if (phone) expect(next!.y, `${name}: below its title`).toBeGreaterThanOrEqual(title!.y + title!.height - 1);
    else expect(Math.abs(next!.y - title!.y), `${name}: on its title's line`).toBeLessThan(6);
  }
  await fits();

  // A row opens its agent's page.
  await press(row("Release notes check"));
  await expect(page).toHaveURL(/\/projects\/harbor\/agents\/[0-9a-f]+$/);
  await walk.state("02-agent-row-opens-its-page", {
    visible: [page.getByRole("heading", { level: 1, name: "Release notes check", exact: true })], hidden: [agents],
  });

  // Before its first reading, then a reading past five minutes; and the computer's zone when the browser is elsewhere.
  const real = await (await page.request.get("/api/monitor")).json();
  let body = { ...real, sessions: real.sessions.map((s: Record<string, unknown>) => s.kind === "agent" ? { ...s, context_percent: null, at: null } : s) };
  await page.route("**/api/monitor*", (route) => route.fulfill({ json: body }));
  await walk.open("/monitor");
  await walk.state("03-session-no-reading-overlay", {
    action: toSessions,
    visible: [sessionRow.getByText(/^\S.* · context unknown · \d+ of 20 min$/)],
    hidden: [sessionRow.locator(".monitor-meter"), sessionRow.locator(".monitor-age")],
  });
  const old = new Date(Date.now() - 12 * 60_000).toISOString();
  body = { ...real, clock: { zone: "Istanbul", offset: real.clock.offset + 180 },
    sessions: real.sessions.map((s: Record<string, unknown>) => s.kind === "agent" ? { ...s, at: old } : s) };
  await walk.open("/monitor");
  await walk.state("04-session-stale-and-zone-overlay", {
    action: toSessions,
    visible: [sessionRow.getByText("Stale", { exact: true }), sessionRow.locator(".monitor-meter[data-stale]"),
      row("Release notes check").getByText(/^Next .* at 07:00 Istanbul$/)],
    hidden: [],
  });
  await fits();

  // No agent to list: no section and no heading.
  body = { ...real, agents: [], sessions: real.sessions.filter((s: Record<string, unknown>) => s.kind !== "agent") };
  await walk.open("/monitor");
  await walk.state("05-no-agents-overlay", {
    action: toSessions,
    visible: [page.getByRole("heading", { name: /^Sessions \(\d+\)$/ })], hidden: [page.getByRole("heading", { name: /^Routines/ }), sessionRow],
  });
});
