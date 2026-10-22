import { expect, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Events you send (design/wireframes/project-agents/PROPOSAL.md, "Events you send (step 4)" and "States") through the
 * real handlers and records of project-agent-events-service.py: the status card's notes, Send it an event with its
 * examples, runs named by their events, the form's trigger menu and event row, and Needs you's paused-agent card.
 */
test.use({ serviceScript: "project-agent-events-service.py" });

const project = "/projects/harbor";

function setup(page: Page, phone: boolean) {
  const press = (target: Locator) => (phone ? target.tap() : target.click());
  const fits = async () => {
    if (phone) expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no horizontal overflow").toBe(true);
  };
  return { press, fits };
}

async function openAgent(page: Page, press: (target: Locator) => Promise<unknown>, name: string) {
  await page.goto(`${project}/agents`);
  await press(page.getByRole("link", { name: new RegExp(`^${name}`) }));
  await expect(page.getByRole("heading", { level: 1, name, exact: true })).toBeVisible();
}

test("an agent with the event trigger: its status, how to send it events, and runs named by their events", async ({ page }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { press, fits } = setup(page, phone);
  const status = page.locator(".agent-status");

  await walk.open(`${project}/agents`);
  await walk.state("e01-agents", {
    visible: [page.getByRole("link", { name: /^Review each merge When it's sent an event/ }),
      page.getByRole("link", { name: /^Issue triage When it's sent an event/ })],
    hidden: [],
  });

  // Running with three events waiting; Recent runs name what each run took.
  await openAgent(page, press, "Review each merge");
  await walk.state("e02-events-waiting", {
    visible: [status.getByText(/^Running · \d+ of 20 min/), status.getByText("3 events wait for the next run.", { exact: true }),
      page.getByRole("link", { name: /^Running.*For “PR #813 merged”$/ }),
      page.getByRole("link", { name: /^Nothing found.*For “PR #812 merged”: Every change in PR #812 has a test\.$/ }),
      page.getByRole("link", { name: /^Found something.*For “PR #810 merged” and 2 more: #811 changed the retry limit without a test\.$/ })],
    hidden: [],
  });

  // Send it an event: the command with Copy and how to try it; Examples folded, then three scripts to copy.
  const send = page.getByRole("group", { name: "Send it an event" });
  const examples = send.locator("details");
  await send.scrollIntoViewIfNeeded();
  await walk.state("e03-send", {
    visible: [send.getByText("From a script or your terminal on this computer:", { exact: true }),
      send.getByText(/^alt --project harbor agent send 'Review each merge' "what happened"$/), send.getByRole("button", { name: "Copy" }),
      send.getByText(/^The examples use its id, \w+, so they keep working after a rename\.$/),
      send.getByText(/^To try it first, use agent run in place of agent send/), send.getByText("Examples", { exact: true }),
      send.getByText("Send numbers and your own words, not text anyone else can write: a run has your code and the network.", { exact: true })],
    hidden: [send.getByText("Each merged pull request: a script for cron every 10 minutes")],
  });
  await press(send.getByText("Examples", { exact: true }));
  const recipe = (what: string) => send.locator(".agent-recipe").filter({ hasText: what });
  await walk.state("e04-send-examples", {
    visible: [recipe("Each merged pull request: a script for cron every 10 minutes").getByRole("button", { name: "Copy" }),
      recipe("Each new issue: a script for cron every 15 minutes").getByRole("button", { name: "Copy" }),
      recipe("When a command of yours fails").getByRole("button", { name: "Copy" })],
    hidden: [],
  });
  await expect(examples).toHaveAttribute("open", "");
  await expect(send.locator(".agent-recipe")).toHaveCount(3);
  // A script keeps its lines and scrolls sideways, and its Copy sits above it, never over the code.
  const merges = recipe("Each merged pull request");
  const code = merges.locator("pre");
  await expect(code).toContainText("gh pr list -R example/harbor --state merged --search sort:updated-desc -L 10");
  expect(await code.evaluate((pre) => getComputedStyle(pre).whiteSpace)).toBe("pre");
  const copy = await merges.getByRole("button", { name: "Copy" }).boundingBox();
  const box = await code.boundingBox();
  expect(copy!.y + copy!.height, "Copy above the code").toBeLessThanOrEqual(box!.y);
  await fits();

  // The run page lists each event it took under what fired it; Run now's replay names the latest.
  await press(page.getByRole("link", { name: /^Found something/ }));
  const events = page.getByLabel("Events");
  await walk.state("e05-run-events", {
    visible: [page.getByText(/^For “PR #810 merged” and 2 more · /), events.getByText(/ · PR #810 merged$/),
      events.getByText(/ · PR #811 merged$/), events.getByText(/ · PR #812 merged$/)],
    hidden: [],
  });
  await page.goBack();
  await press(page.getByRole("link", { name: /^Nothing found/ }));
  await walk.state("e06-run-replay", {
    visible: [page.getByText(/^Requested by you · For “PR #812 merged”, the latest · /), page.getByLabel("Events").getByText(/ · PR #812 merged$/)],
    hidden: [],
  });

  // Not on yet with no event: Run now runs without one, and the card says so beforehand.
  await openAgent(page, press, "Issue triage");
  await walk.state("e07-no-event-yet", {
    visible: [status.getByText(/^Not on yet/), status.getByText("No event yet, so Run now runs without one.", { exact: true }),
      status.getByRole("button", { name: "Run now" }), status.getByRole("button", { name: "Turn on" }), page.getByRole("group", { name: "Send it an event" })],
    hidden: [],
  });
  await fits();
});

test("the form: + Add a trigger's menu, the event row and its hint, then schedules added at once", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { press, fits } = setup(page, phone);
  const add = page.getByRole("button", { name: "+ Add a trigger" });
  const menu = page.getByRole("menu", { name: "Add a trigger" });
  const eventRow = page.getByRole("group", { name: "When it’s sent an event" });
  const hint = page.getByText("Events that arrive during a run go to its next run together.", { exact: true });

  await walk.open(`${project}/agents/new`);
  await press(add);
  await walk.state("e08-trigger-menu", {
    visible: [menu.getByRole("menuitem", { name: "On a schedule" }), menu.getByRole("menuitem", { name: "When it’s sent an event" })],
    hidden: [eventRow, hint],
  });
  await expect(menu.getByRole("menuitem", { name: "On a schedule" })).toBeFocused();
  if (phone) {
    for (const item of await menu.getByRole("menuitem").all()) expect((await item.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }
  await page.keyboard.press("Escape");
  await walk.state("e09-menu-closed", { visible: [add], hidden: [menu] });
  await expect(add).toBeFocused();

  await press(add);
  await press(menu.getByRole("menuitem", { name: "When it’s sent an event" }));
  await walk.state("e10-event-row", {
    visible: [eventRow.getByText("You send it from this computer; its page shows how.", { exact: true }), hint,
      page.getByRole("group", { name: "Schedule 1" })],
    hidden: [menu, page.getByText("Only when you run it")],
  });
  await expect(eventRow.getByRole("button", { name: "Remove this trigger" })).toBeFocused();
  if (phone) expect((await eventRow.getByRole("button", { name: "Remove this trigger" }).boundingBox())!.height).toBeGreaterThanOrEqual(44);

  await press(add);
  await walk.state("e11-schedule-added", { visible: [page.getByRole("group", { name: "Schedule 2" })], hidden: [menu] });

  await press(eventRow.getByRole("button", { name: "Remove this trigger" }));
  await walk.state("e12-event-removed", { visible: [add], hidden: [eventRow, hint] });
  await expect(add).toHaveAttribute("aria-haspopup", "menu");
  await fits();
});

test("Needs you: an agent that paused itself, Resume refused and then resumed", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { press, fits } = setup(page, phone);
  const card = page.getByRole("article", { name: "Flaky tests" });
  const resume = card.getByRole("button", { name: "Resume", exact: true });

  await walk.open("/");
  await walk.state("e13-needs-you", {
    visible: [page.getByText("1 paused agent in harbor", { exact: true }), card.getByText("Agent paused itself", { exact: true }),
      card.getByRole("link", { name: "Flaky tests" }), card.getByText(/^3 runs in a row couldn't finish\. The last: 'make test-e2e' is not a target\.$/),
      resume, card.getByRole("link", { name: "Open the last run" })],
    hidden: [card.getByRole("link", { name: "Open L2 chat" })],
  });
  await fits();

  // Retired in another window: the button returns with why.
  await page.route("**/api/agents", (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "Flaky tests is retired." }) }) : route.continue());
  await press(resume);
  await walk.state("e14-resume-refused", { visible: [card.getByText("Not resumed: Flaky tests is retired.", { exact: true }), resume], hidden: [] });
  await page.unrouteAll({ behavior: "wait" });

  // Resume spins until the agent reads as on, and the card leaves.
  let release = () => {};
  const released = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/agents", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await released;
    await route.continue();
  });
  await press(resume);
  await walk.state("e15-resuming", { visible: [card.getByRole("button", { name: "Resume, saving" })], hidden: [resume] });
  release();
  await page.unrouteAll({ behavior: "wait" });
  await walk.state("e16-resumed", { visible: [page.getByText("Nothing needs you.", { exact: true })], hidden: [card] });
});
