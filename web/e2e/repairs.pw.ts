import { expect, type Page, type Route } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Repairs (SPEC.md §3.7, §3.15, docs/OPERATIONS.md#repairs), walked at both widths: Settings → About's Repair… and All
 * repairs, the start sheet with its refusals, Work's Altitude repairs section, a repair's page in a task's form while
 * it works, asks and runs low on time, how it ends (Stop, the time limit, a report with Altitude's check, no report),
 * a terminal repair's page, a repair's question in Needs you, and Monitor without a repair. The service is real; the
 * repairs are fictional fixtures behind routes, so no agent runs and nothing gets full access.
 */

type Json = Record<string, unknown>;
const id = (digit: string) => digit.repeat(32);

// An overview or monitor read still in flight at teardown would find its response disposed.
test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "wait" }); });
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const APP = { id: id("a"), title: "The page does not load on my phone", route: "app", engine: "claude", started: ago(12), ended: null, outcome: null, status: "working" };
// Ended 71 hours ago stays in Work; ended 73 hours ago has left it (the 72-hour boundary).
const ROWS = [
  APP,
  { id: id("b"), title: "Failed update to 0.9.0", route: "app", engine: "claude", started: ago(71 * 60 + 20), ended: ago(71 * 60), outcome: "repaired", status: "repaired" },
  { id: id("c"), title: "Tasks stopped starting after I moved the projects folder somewhere else entirely, then back again", route: "terminal", engine: "claude", started: ago(150), ended: ago(120), outcome: "changed_by_hand", status: "changed_by_hand" },
  { id: id("d"), title: "Repair", route: "terminal", engine: "claude", started: ago(73 * 60 + 5), ended: ago(73 * 60), outcome: "no_report", status: "no_report" },
];

const STEPS = [
  { kind: "event", at: ago(12), event: "started" },
  { kind: "you", at: ago(12), text: "The page doesn't load on my phone since this morning." },
  { kind: "say", at: ago(11), text: "I'll check the service and its address first." },
  { kind: "step", at: ago(11), verb: "ran", text: "systemctl --user status altitude", detail: "systemctl --user status altitude", output: "active (running)", exit: null },
  { kind: "step", at: ago(10), verb: "failed", text: "ls ~/.altitude/certs/phone", detail: "ls ~/.altitude/certs/phone", output: "No such file or directory", exit: 2 },
  { kind: "step", at: ago(9), verb: "changed", text: "~/.config/altitude/altitude.env", detail: "~/.config/altitude/altitude.env", output: "", exit: null },
  { kind: "step", at: ago(8), verb: "fetched", text: "github.com/example/altitude/releases", detail: "github.com/example/altitude/releases", output: "", exit: null },
  { kind: "event", at: ago(7), event: "restarting" },
  { kind: "event", at: ago(6), event: "back" },
  { kind: "step", at: ago(1), verb: "running", text: "alt doctor --json", detail: "alt doctor --json", output: "", exit: null },
];
const QUESTION = { kind: "question", at: ago(1), id: "q1", type: "ask", title: "Go ahead?", reason: "The phone's certificate expired; renewing it restarts Altitude once.",
  command: "alt certificate renew", answer: null, answered: null };
const YOUR_STEP = { kind: "question", at: ago(1), id: "q2", type: "step", title: "Your step: trust the new certificate",
  reason: null, command: "sudo update-ca-certificates", answer: null, answered: null };
const REPORT = { wrong: "The phone's certificate had expired.", changed: "Renewed the certificate and restarted Altitude.", remains: "Nothing." };
const CHECK = { at: ago(0), version: "v0.9.0", running: "v0.9.0", matches: true, failing: [] };

function running(extra: Json = {}): Json {
  return { ...APP, problem: APP.title, model: "claude-opus-5-5", update: null, report: null, check: null, brief: "# Repair brief", end: null,
    stop: null, stop_unconfirmed: null, left_running: null, timeline: STEPS, left: 108 * 60, keep_going: false, stopping: false, ...extra };
}

function ended(end: string, extra: Json = {}): Json {
  return running({ ended: ago(0), end, status: "checking", left: null, timeline: STEPS.map((step) => step.verb === "running" ? { ...step, verb: "cut" } : step), ...extra });
}

/** One repair's record behind every read and action route; `answer` decides what each POST returns. */
async function repairs(page: Page, state: { listing: Json; detail: Record<string, Json>; post?: (path: string, body: Json) => Promise<{ status: number; json: Json }> }) {
  const posts: { path: string; body: Json }[] = [];
  await page.route("**/api/repairs", async (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: state.listing });
    const body = route.request().postDataJSON() as Json;
    posts.push({ path: "/api/repairs", body });
    const reply = await state.post!("/api/repairs", body);
    return route.fulfill({ status: reply.status, json: reply.json });
  });
  await page.route(/\/api\/repairs\/[^/]+(\/[a-z-]+)?$/, async (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as Json;
      posts.push({ path, body });
      const reply = await state.post!(path, body);
      return route.fulfill({ status: reply.status, json: reply.json });
    }
    const found = state.detail[path.split("/").pop()!];
    return found ? route.fulfill({ json: found }) : route.fulfill({ status: 404, json: { error: "That repair is not on this computer." } });
  });
  return posts;
}


/** The repair page's status: the phone header's status line, or the desktop header's chips. */
const statusOf = (page: Page, phone: boolean) => page.locator(phone ? ".phone-status" : ".task-chips");

test("Settings → About: Repair…, the start sheet, its refusals and All repairs", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const about = page.getByRole("region", { name: "About" });
  const show = () => about.scrollIntoViewIfNeeded();
  const intro = about.getByText("Something not working? A repair agent finds out why and fixes it, with full access while it works.");
  const start = about.getByRole("button", { name: "Repair…" });
  const allRepairs = about.getByRole("link", { name: "All repairs" });
  const row = (title: string | RegExp) => page.getByRole("link", { name: title });

  const state = { listing: { running: null, engine: "Claude", repairs: [] } as Json, detail: {} as Record<string, Json>,
    post: async (_path: string, _body: Json) => ({ status: 200, json: running() }) };
  const posts = await repairs(page, state);

  await walk.open("/settings");
  await walk.state("01-no-repairs", { action: show, visible: [intro, start], hidden: [allRepairs, page.getByText(/Loading repairs/), page.getByRole("alert")] });

  // The sheet: what full access means, the field, the plan line; Start waits for words.
  await start.click();
  const sheet = page.getByRole("dialog", { name: "Start a repair" });
  const field = sheet.getByRole("textbox", { name: "What’s wrong?" });
  const go = sheet.getByRole("button", { name: /Start repair/ });
  await walk.state("02-sheet-empty", {
    visible: [sheet.getByRole("heading", { name: "Start a repair?" }), sheet.getByText("full access to this computer as you"),
      sheet.getByText(/Its rules are instructions it follows, not limits Altitude enforces\. .* what it reads goes to Claude\./),
      field, sheet.getByText("Claude · uses your plan like a small task"), go, sheet.getByRole("button", { name: "Cancel" })],
    hidden: [sheet.getByRole("alert")],
  });
  await expect(field).toHaveAttribute("placeholder", "For example: the page doesn’t load on my phone");
  await expect(go).toBeDisabled();
  await expect(field).toBeFocused();
  const before = (await field.boundingBox())!.height;
  await field.fill("The page doesn't load on my phone.\nIt worked yesterday.\nThe desktop is fine.\nI restarted the phone.");
  expect((await field.boundingBox())!.height, "the field grows with its text").toBeGreaterThan(before);
  await expect(go).toBeEnabled();

  // A repair already running: its reason and Open it; one in a terminal, and no agent, only their reasons.
  let refusal: { status: number; json: Json } = { status: 409, json: { error: "A repair is already running.", running: id("a") } };
  state.post = async () => refusal;
  await go.click();
  const refused = sheet.getByRole("alert");
  await walk.state("03-sheet-already-running", { visible: [refused.getByText("A repair is already running."), refused.getByRole("link", { name: "Open it" }), go], hidden: [] });
  refusal = { status: 409, json: { error: "No coding agent can run now: Claude's weekly allowance is used. Run `alt repair` in a terminal instead." } };
  await go.click();
  await walk.state("04-sheet-no-agent", {
    visible: [refused.getByText(/weekly allowance is used/), refused.locator("code", { hasText: "alt repair" })],
    hidden: [refused.getByRole("link", { name: "Open it" })],
  });

  // Starting holds Start repair as Starting…; once accepted the page opens, and the same request identity was sent each time.
  let accept: () => void = () => {};
  const accepted = new Promise<void>((resolve) => { accept = resolve; });
  state.detail[id("a")] = running({ timeline: STEPS.slice(0, 2) });
  state.post = async () => { await accepted; return { status: 200, json: state.detail[id("a")]! }; };
  await go.click();
  await walk.state("05-sheet-starting", { visible: [sheet.getByRole("button", { name: "Start repair, starting" }), sheet.getByText("Starting…")], hidden: [refused] });
  await expect(sheet.getByRole("button", { name: "Cancel" })).toBeDisabled();
  accept();
  await expect(page).toHaveURL(new RegExp(`/repairs/${id("a")}$`));
  const requests = new Set(posts.filter((post) => post.path === "/api/repairs").map((post) => post.body.request));
  expect(requests.size, "one sheet sends one request identity").toBe(1);
  expect(posts.at(-1)!.body.problem).toContain("It worked yesterday.");

  // Back in Settings: the running repair replaces Repair…, and All repairs leads to every repair; no list of its own.
  state.listing = { running: { id: id("a"), route: "app", title: APP.title }, engine: "Claude", repairs: ROWS };
  await page.goto("/settings");
  await walk.state("06-running", {
    action: show,
    visible: [intro, about.getByText("Running ·"), about.getByRole("link", { name: "Open", exact: true }), allRepairs],
    hidden: [start, about.getByRole("link", { name: /Failed update to 0\.9\.0/ }), about.locator(".task-card")],
  });
  await expect(about.getByRole("link", { name: "Open", exact: true })).toHaveAttribute("href", `/repairs/${id("a")}`);

  state.listing = { running: { id: null, route: "terminal" }, engine: "Claude", repairs: ROWS.slice(1) };
  await page.reload();
  await walk.state("07-running-in-terminal", { action: show, visible: [about.getByText("Running in a terminal"), allRepairs], hidden: [start, about.getByRole("link", { name: "Open", exact: true })] });

  // All repairs keeps every repair, including those that have left Work, in Work's rows.
  state.listing = { running: null, engine: "Claude", repairs: ROWS.slice(1).concat({ ...ROWS[3]!, id: id("e"), status: "stopped", outcome: null }) };
  await page.reload();
  await allRepairs.click();
  await expect(page).toHaveURL(/\/settings\/repairs$/);
  const byHand = row(/^Tasks stopped starting/);
  await walk.state("08-all-repairs", {
    visible: [page.getByRole("heading", { name: "All repairs", exact: true }), row(/^Failed update to 0\.9\.0 · ✓ Repaired · ended 2d ago$/),
      byHand.getByText("Confirm yourself · from a terminal · ended 2h ago"), row(/^Repair · ✕ Not repaired · from a terminal · ended 3d ago$/),
      row(/^Repair · Stopped · from a terminal · ended 3d ago$/)],
    hidden: [intro],
  });
  expect(await byHand.locator(".task-card-title").evaluate((node) => node.scrollWidth > node.clientWidth), "a long title stays on one line").toBe(true);
  state.listing = { running: null, engine: null, repairs: [] };
  await page.reload();
  await walk.state("09-all-repairs-empty", { visible: [page.getByText("No repairs yet. Start one with Repair… in Settings → About.")], hidden: [byHand] });
});

test("Work's Altitude repairs: hidden with none, running, waiting and finished rows, and the 72-hour boundary", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const work = page.getByRole("region", { name: "Work", exact: true });
  const section = work.getByRole("region", { name: "Altitude repairs" });
  const row = (title: RegExp) => section.getByRole("link", { name: title });
  const state = { listing: { running: null, engine: "Claude", repairs: [] } as Json, detail: { [id("a")]: running() } as Record<string, Json> };
  await repairs(page, state);
  let fail = false;
  let failed = 0;
  let reads = 0;
  await page.route("**/api/repairs", (route) => {
    if (route.request().method() === "GET") reads += 1;
    if (!fail || route.request().method() !== "GET") return route.fallback();
    failed += 1;
    return route.fulfill({ status: 500, json: { error: "fixture" } });
  });

  const openWork = async () => {
    const before = reads;
    await walk.open(phone ? "/projects/atlas?tab=work" : "/projects/atlas");
    await expect(work.getByText(/current · \d+ done this week/)).toBeVisible();
    await expect.poll(() => reads, "Work read the repairs listing").toBeGreaterThan(before);
  };
  await openWork();
  await walk.state("01-no-repairs", { visible: [work], hidden: [section, work.getByText("Altitude repairs")] });

  // A failed read shows nothing either: no error line for something the operator did not ask about.
  fail = true;
  state.listing = { running: null, engine: "Claude", repairs: ROWS };
  await openWork();
  await expect.poll(() => failed).toBeGreaterThan(0);
  await walk.state("02-read-failed", { visible: [work], hidden: [section, work.getByText("Could not load repairs")] });

  // Running and finished: the running repair first, finished ones for 72 hours after they ended, in task rows.
  fail = false;
  await openWork();
  const title = section.getByRole("heading", { name: "Altitude repairs (3)" });
  await walk.state("03-running-and-finished", {
    action: () => section.scrollIntoViewIfNeeded(),
    visible: [title, row(/^The page does not load on my phone · Repair working · started 1\d min ago$/),
      row(/^Failed update to 0\.9\.0 · ✓ Repaired · ended 2d ago$/), row(/^Tasks stopped starting .* · Confirm yourself · from a terminal · ended 2h ago$/)],
    hidden: [row(/^Repair · /), section.getByText("Your turn · the repair asks")],
  });
  expect(await section.locator(".task-card-dot").evaluateAll((dots) => dots.map((dot) => dot.getAttribute("data-state")))).toEqual(["running", "idle", "idle"]);
  // The section is last in Work: after Current (or the empty line), Done this week and Routines.
  const order = await work.locator(".wp-section, .wp-fold, p.text-muted, .wp-agents").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label") ?? node.className));
  expect(order.at(-1), "repairs come last").toBe("Altitude repairs");

  // A failed refresh keeps the rows it has: the running repair's listing is read again while it runs.
  fail = true;
  const seen = failed;
  await expect.poll(() => failed, { timeout: 20_000 }).toBeGreaterThan(seen);
  await walk.state("04-refresh-failed", { action: () => section.scrollIntoViewIfNeeded(), visible: [title, row(/^Failed update to 0\.9\.0/)], hidden: [work.getByText("Could not load repairs")] });
  fail = false;

  // Waiting: Your turn, as on a task's row.
  state.listing = { running: { id: id("a"), route: "app", title: APP.title }, engine: "Claude", repairs: [{ ...APP, status: "waiting" }, ...ROWS.slice(1)] };
  await openWork();
  const waiting = row(/^The page does not load on my phone · Your turn · the repair asks · Repair waiting · started 1\d min ago$/);
  await walk.state("05-waiting", { action: () => section.scrollIntoViewIfNeeded(), visible: [waiting, waiting.getByText("Your turn · the repair asks")], hidden: [] });

  // Only finished repairs, and one in a terminal still running.
  state.listing = { running: { id: null, route: "terminal" }, engine: "Claude",
    repairs: [{ ...ROWS[2]!, id: id("f"), ended: null, outcome: null, status: "working" }, ROWS[1], ROWS[3],
      { ...ROWS[2]!, id: id("g"), title: "Voice input stopped working", started: ago(300), ended: null, outcome: null, status: "checking" }] };
  await openWork();
  await walk.state("06-terminal-running", {
    action: () => section.scrollIntoViewIfNeeded(),
    // A terminal record whose end is not settled yet says when it started.
    visible: [section.getByRole("heading", { name: "Altitude repairs (3)" }), row(/ · Repair working in a terminal · started 2h ago$/),
      row(/^Voice input stopped working · Checking the result · from a terminal · started 5h ago$/)],
    hidden: [row(/^Repair · /)],
  });

  // A row opens the repair's page; Work stays lit on the phone and the crumb leads back to it.
  state.listing = { running: { id: id("a"), route: "app", title: APP.title }, engine: "Claude", repairs: ROWS };
  await openWork();
  await row(/^The page does not load on my phone/).click();
  await expect(page).toHaveURL(new RegExp(`/repairs/${id("a")}$`));
  await expect(page.getByRole("heading", { level: 1, name: APP.title })).toBeVisible();
  if (phone) {
    await walk.state("07-opened-from-work", {
      visible: [page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Work", exact: true })], hidden: [] });
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Work", exact: true })).toHaveAttribute("aria-current", "page");
    await page.getByRole("button", { name: "Back", exact: true }).click();
  } else {
    await walk.state("07-opened-from-work", { visible: [page.getByRole("button", { name: "‹ Work" })], hidden: [] });
    await page.getByRole("button", { name: "‹ Work" }).click();
  }
  await expect(page).toHaveURL(/\/projects\/atlas/);
  await expect(section).toBeVisible();
});

test("a repair's page while it works: steps, messages, questions, your step and the time limit", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const state = { listing: { running: { id: id("a"), route: "app", title: APP.title }, engine: "Claude", repairs: ROWS } as Json,
    detail: { [id("a")]: running() } as Record<string, Json>,
    post: async (_path: string, _body: Json) => ({ status: 200, json: state.detail[id("a")]! }) };
  const posts = await repairs(page, state);
  const heading = page.getByRole("heading", { level: 1, name: APP.title });
  const stop = page.getByRole("button", { name: /^Stop/ });
  const composer = page.getByRole("textbox", { name: "Message the repair" });
  const status = statusOf(page, phone);
  const step = (text: string) => page.locator(".repair-step").filter({ hasText: text });
  const turn = page.locator(".conversation-question");

  await walk.open(`/repairs/${id("a")}`);
  await walk.state("01-working", {
    visible: [heading, stop, ...(phone ? [status.getByText("Repair working"), status.getByText("· 1 h 48 min left")]
        : [page.getByRole("button", { name: "‹ Work" }), status.getByText("Repair working"), status.getByText(/^Opus 5 on /), status.getByText("Full access · 1 h 48 min left")]),
      page.getByText("Brief", { exact: true }),
      page.getByText("Started by you · full access for 2 hours"), page.getByText("The page doesn't load on my phone since this morning."),
      page.getByText("I'll check the service and its address first."),
      step("systemctl --user status altitude").getByText("1 line"), step("ls ~/.altitude/certs/phone").getByText("exit 2"),
      step("altitude.env").getByText("changed"), step("releases").getByText("fetched"), step("alt doctor --json").locator(".session-hint .spinner"),
      page.getByText("Altitude is restarting · the repair continues"), page.getByText("Altitude is back"), composer],
    hidden: [page.getByRole("button", { name: "Keep going" }), page.getByText("Your turn"), page.getByRole("tab"), page.getByText("active (running)")],
  });
  await expect(step("ls ~/.altitude/certs/phone")).toHaveAttribute("data-error", "true");
  if (!phone) await expect(stop.locator("kbd")).toHaveText("Esc");
  // The brief opens from its whole row.
  await page.locator(".repair-brief > summary").click();
  await expect(page.locator(".repair-brief pre")).toHaveText("# Repair brief");
  const ran = step("systemctl --user status altitude");
  await ran.locator("summary").click();
  await walk.state("02-step-open", { visible: [ran.getByText("active (running)"), page.locator(".repair-brief pre")], hidden: [] });

  // A message joins the conversation as yours and the agent reads it at its next turn.
  state.post = async (path, body) => {
    if (path.endsWith("/message")) state.detail[id("a")] = running({ timeline: [...STEPS, { kind: "you", at: ago(0), text: body.text }] });
    return { status: 200, json: state.detail[id("a")]! };
  };
  await composer.fill("The phone is on the home Wi-Fi.");
  await composer.press("Enter");
  await walk.state("03-message-sent", { visible: [page.locator(".repair-you").getByText("The phone is on the home Wi-Fi.")], hidden: [] });
  await expect(composer).toHaveValue("");
  expect(posts.filter((post) => post.path.endsWith("/message"))).toHaveLength(1);

  // The repair asks before a big change, under Your turn; Go ahead settles it and the turn passes.
  state.detail[id("a")] = running({ status: "waiting", timeline: [...STEPS, QUESTION] });
  await page.reload();
  const card = page.getByRole("article", { name: "Go ahead?" });
  await walk.state("04-question", {
    visible: [status.getByText("Repair waiting for you"), turn.getByText("Your turn · the repair asks"), card.getByText(QUESTION.reason),
      card.getByText(QUESTION.command), card.getByRole("button", { name: "Go ahead" }), card.getByRole("button", { name: "Don’t" })],
    hidden: [card.getByText("Went ahead · by you"), card.getByText("Asked by the repair")],
  });
  state.post = async (_path, body) => {
    state.detail[id("a")] = running({ timeline: [...STEPS, { ...QUESTION, answer: body.answer, answered: ago(0) }] });
    return { status: 200, json: state.detail[id("a")]! };
  };
  await card.getByRole("button", { name: "Go ahead" }).click();
  await walk.state("05-went-ahead", { visible: [card.getByText("Went ahead · by you")], hidden: [page.getByText("Your turn"), card.getByRole("button", { name: "Go ahead" })] });
  await expect(turn).toHaveAttribute("data-historical", "true");
  expect(posts.at(-1)).toMatchObject({ path: `/api/repairs/${id("a")}/answer`, body: { question: "q1", answer: "yes" } });

  // A step that needs the operator's password: Copy and I ran it.
  state.detail[id("a")] = running({ status: "waiting", timeline: [...STEPS, YOUR_STEP] });
  await page.reload();
  const yours = page.getByRole("article", { name: YOUR_STEP.title });
  await walk.state("06-your-step", {
    visible: [turn.getByText("Your turn · a step for your terminal"), yours.getByText(YOUR_STEP.command), yours.getByRole("button", { name: "Copy" }), yours.getByRole("button", { name: "I ran it" })],
    hidden: [yours.getByRole("button", { name: "Go ahead" })],
  });
  state.post = async (_path, body) => {
    state.detail[id("a")] = running({ timeline: [...STEPS, { ...YOUR_STEP, answer: body.answer, answered: ago(0) }] });
    return { status: 200, json: state.detail[id("a")]! };
  };
  await yours.getByRole("button", { name: "I ran it" }).click();
  await walk.state("07-you-ran-it", { visible: [yours.getByText("Done · you ran it")], hidden: [yours.getByRole("button", { name: "I ran it" })] });

  // Fifteen minutes before the end the conversation closes with time left and Keep going; it adds an hour.
  state.detail[id("a")] = running({ left: 14 * 60, keep_going: true });
  await page.reload();
  const keep = page.getByRole("button", { name: "Keep going" });
  const closing = page.locator(".repair-keep");
  await walk.state("08-time-low", { visible: [closing.getByText(/14 min of full access left ·/), keep, status.getByText(phone ? "· 14 min left" : "Full access · 14 min left")], hidden: [] });
  state.post = async () => {
    state.detail[id("a")] = running({ left: 74 * 60, timeline: [...STEPS, { kind: "event", at: ago(0), event: "kept_going" }] });
    return { status: 200, json: state.detail[id("a")]! };
  };
  await keep.click();
  await walk.state("09-kept-going", { visible: [status.getByText(phone ? "· 1 h 14 min left" : "Full access · 1 h 14 min left"), page.getByText("Kept going · one more hour")], hidden: [keep] });
  expect(posts.filter((post) => post.path.endsWith("/keep-going"))).toHaveLength(1);

  // Altitude stops answering: after the read's retries the page keeps what it has and says so.
  await page.route(/\/api\/repairs\/[^/]+$/, (route) => route.request().method() === "GET" ? route.abort() : route.fallback());
  const silent = page.getByText("Altitude is not answering · the repair continues and this page catches up when it is back");
  await expect(silent).toBeVisible({ timeout: 30_000 });
  await walk.state("10-not-answering", { visible: [silent, heading, composer], hidden: [] });
});

test("how a repair ends: Stop, the time limit, its report and Altitude's check, no report, and a terminal repair", async ({ page }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const state = { listing: { running: { id: id("a"), route: "app", title: APP.title }, engine: "Claude", repairs: ROWS } as Json,
    detail: { [id("a")]: running({ timeline: [...STEPS, QUESTION] }) } as Record<string, Json>,
    post: async (_path: string, _body: Json) => ({ status: 200, json: state.detail[id("a")]! }) };
  const posts = await repairs(page, state);
  const stop = page.getByRole("button", { name: /^Stop/ });
  const composer = page.getByRole("textbox", { name: "Message the repair" });
  const status = statusOf(page, phone);
  const closing = page.locator(".repair-keep");
  const continueRepair = page.getByRole("button", { name: "Continue repair…" });

  await walk.open(`/repairs/${id("a")}`);
  await stop.click();
  const confirm = page.getByRole("dialog", { name: "Stop the repair?" });
  await walk.state("01-stop-asks", {
    visible: [confirm.getByRole("heading", { name: "Stop the repair?" }), confirm.getByText("A command running now is cut off."),
      confirm.getByRole("button", { name: "Stop repair" }), confirm.getByRole("button", { name: "Keep running" })],
    hidden: [],
  });
  await confirm.getByRole("button", { name: "Keep running" }).click();
  await expect(confirm).toBeHidden();
  await expect(composer).toBeVisible();
  if (!phone) {
    // Escape asks too on a computer, as a task's Stop does.
    await page.locator(".repair-sys").first().click();
    await page.keyboard.press("Escape");
    await expect(confirm).toBeVisible();
    await confirm.getByRole("button", { name: "Keep running" }).click();
  }

  let finish: () => void = () => {};
  const finished = new Promise<void>((resolve) => { finish = resolve; });
  state.post = async () => { await finished; return { status: 200, json: state.detail[id("a")]! }; };
  await stop.click();
  await confirm.getByRole("button", { name: "Stop repair" }).click();
  await walk.state("02-stopping", { visible: [page.getByRole("button", { name: /Stopping…/ })], hidden: [composer, confirm] });
  state.detail[id("a")] = ended("stop", { status: "stopped", stop: { at: ago(0), update: false },
    left_running: [{ kind: "service", name: "altitude-tunnel.service" }],
    timeline: [...STEPS.map((step) => step.verb === "running" ? { ...step, verb: "cut" } : step), { ...QUESTION, answer: "expired", answered: ago(0) }] });
  finish();
  const card = page.getByRole("article", { name: "Go ahead?" });
  await walk.state("03-stopped", {
    visible: [status.getByText("Stopped"), page.getByText("Stopped by you · the agent and its processes were stopped"),
      page.locator(".repair-step").filter({ hasText: "alt doctor --json" }).getByText("cut off"),
      card.getByText("Expired · the session ended"), page.getByRole("heading", { name: "Left running" }), page.getByText("altitude-tunnel.service")],
    hidden: [stop, composer, card.getByRole("button", { name: "Go ahead" }), continueRepair, page.getByText("The repair ended without a report"),
      page.getByText(/Full access ·/), page.getByText("Your turn")],
  });

  state.detail[id("a")] = ended("stop", { status: "stopped", stop: { at: ago(0), update: true }, left_running: [] });
  await page.reload();
  await walk.state("04-stopped-during-update", { visible: [page.getByText("Stopped by you · the update it started continues; its result appears here")], hidden: [page.getByText("Left running")] });

  state.detail[id("a")] = running({ stop: { at: ago(0), update: false }, stop_unconfirmed: true });
  await page.reload();
  await walk.state("05-stop-unconfirmed", { visible: [page.getByText("Stop unconfirmed · The repair may still be running."), page.getByRole("button", { name: "Check status" })], hidden: [composer] });
  // Check status asks the job to stop again.
  state.post = async () => ({ status: 200, json: state.detail[id("a")]! });
  const stops = posts.filter((post) => post.path.endsWith("/stop")).length;
  await page.getByRole("button", { name: "Check status" }).click();
  await expect.poll(() => posts.filter((post) => post.path.endsWith("/stop")).length).toBe(stops + 1);

  // The time ran out: Keep going resumes the same session for an hour, or says why it could not.
  state.detail[id("a")] = ended("time", { status: "checking", keep_going: true });
  await page.reload();
  const keep = page.getByRole("button", { name: "Keep going" });
  await walk.state("06-time-up", { visible: [closing.getByText(/Time’s up · the session ended ·/), keep], hidden: [composer, stop, page.getByText("The repair ended without a report")] });
  state.post = async () => ({ status: 409, json: { error: "Couldn’t resume: No coding agent can run now: Claude's weekly allowance is used." } });
  await keep.click();
  await walk.state("07-keep-going-failed", { visible: [closing.getByText(/Couldn’t resume: No coding agent can run now/)], hidden: [] });
  await expect(closing.locator(".text-danger")).toBeVisible();

  // The report, then Altitude's check: Repaired, Confirm yourself, Not repaired.
  state.detail[id("a")] = ended("report", { status: "checking", report: REPORT, left_running: [] });
  await page.reload();
  await walk.state("08-report-checking", {
    visible: [status.getByText("Checking the result"), page.getByText("Altitude is checking the result…"), page.getByText(REPORT.wrong), continueRepair],
    hidden: [composer, stop, page.getByText(/^Altitude checked:/)],
  });
  state.detail[id("a")] = ended("report", { status: "repaired", outcome: "repaired", report: REPORT, check: CHECK, left_running: [] });
  await page.reload();
  const outcome = page.getByRole("region", { name: "Report" });
  await walk.state("09-repaired", {
    visible: [status.getByText(phone ? "Repaired" : "✓ Repaired", { exact: true }), outcome.getByRole("heading", { name: "Repaired", exact: true }), outcome.getByText("✓"),
      outcome.getByText("What was wrong"), outcome.getByText(REPORT.wrong), outcome.getByText("What changed"), outcome.getByText("What remains"),
      outcome.getByText("Altitude checked: version 0.9.0 · matches its release · service running · all checks pass"), outcome.getByText("Nothing new"), continueRepair],
    hidden: [outcome.getByText("Try:"), composer],
  });
  if (phone) await expect(status.getByText("✓")).toHaveCount(0);
  state.detail[id("a")] = ended("report", { status: "confirm", outcome: "confirm", report: { ...REPORT, try: "Open Altitude on your phone." }, check: CHECK, left_running: [] });
  await page.reload();
  await walk.state("10-confirm-yourself", { visible: [outcome.getByRole("heading", { name: "Confirm yourself" }), outcome.getByText("Try:"), outcome.getByText("Open Altitude on your phone.")], hidden: [] });
  state.detail[id("a")] = ended("report", { status: "not_repaired", outcome: "not_repaired", report: REPORT, left_running: [{ kind: "timer", name: "altitude-retry.timer" }],
    check: { ...CHECK, failing: [{ name: "github cli" }] } });
  await page.reload();
  await walk.state("11-not-repaired", {
    visible: [outcome.getByRole("heading", { name: "Not repaired", exact: true }), outcome.locator(".text-danger", { hasText: "failing: github cli" }), outcome.getByText("altitude-retry.timer")],
    hidden: [outcome.getByText(/all checks pass/), outcome.getByText("Nothing new")],
  });
  await continueRepair.click();
  await expect(page.getByRole("dialog", { name: "Start a repair" })).toBeVisible();
  await page.keyboard.press("Escape");

  // The agent stopped on an error without a report: Repair again.
  state.detail[id("a")] = ended("failed", { status: "no_report", outcome: "no_report", check: CHECK,
    timeline: [...STEPS, { kind: "event", at: ago(0), event: "failed", text: "the coding agent exited with status 1" }] });
  await page.reload();
  await walk.state("12-no-report", {
    visible: [page.getByText("The agent stopped: the coding agent exited with status 1"), page.getByText("The repair ended without a report"), page.getByRole("button", { name: "Repair again" })],
    hidden: [continueRepair, composer],
  });

  // A terminal repair: its brief, start and end, report and check; its steps stay in that terminal.
  state.detail[id("c")] = { ...ROWS[2], problem: ROWS[2]!.title, model: null, update: null, brief: "# Repair brief",
    report: { wrong: "The service still pointed at the old projects folder.", changed: "Set the new folder.", remains: "Nothing known.", try: "Start a task in atlas." },
    check: { ...CHECK, matches: false } };
  await page.goto(`/repairs/${id("c")}`);
  await walk.state("13-terminal-repair", {
    visible: [status.getByText("Confirm yourself"), ...(phone ? [] : [status.getByText("From a terminal")]),
      page.getByText(/^Started from a terminal · /), page.getByText(/^Ended · /), page.getByText("Its steps are in that terminal’s history."),
      page.getByRole("heading", { name: "Confirm yourself · the installation was changed by hand" }), page.getByText("its files differ from the release"),
      page.getByText("Brief", { exact: true })],
    hidden: [stop, page.getByText(/Full access/), composer, page.getByRole("link", { name: "All repairs" })],
  });

  // Before any project exists, a repair's way back is Settings and no tab is lit.
  await page.route("**/api/overview*", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...((await response.json()) as Json), projects: [] } });
  });
  await page.reload();
  if (phone) {
    await walk.state("14-no-project", { visible: [page.getByRole("button", { name: "Back", exact: true })], hidden: [] });
    await expect(page.getByRole("navigation", { name: "Primary" }).locator("[aria-current=page]")).toHaveCount(0);
  } else {
    await walk.state("14-no-project", { visible: [page.getByRole("button", { name: "‹ Settings" })], hidden: [page.getByRole("button", { name: "‹ Work" })] });
    await page.getByRole("button", { name: "‹ Settings" }).click();
    await expect(page).toHaveURL(/\/settings$/);
  }
  await page.unroute("**/api/overview*");

  await page.goto(`/repairs/${id("f")}`);
  await walk.state("15-missing-repair", { visible: [page.getByText("That repair is not on this computer.")], hidden: [page.getByRole("alert")] });
});

test("a repair's question in Needs you, and Monitor without a repair", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const question = { id: "q1", kind: "ask", title: "Go ahead?", reason: QUESTION.reason, command: QUESTION.command, asked: ago(1), answer: null,
    repair: id("a"), repair_title: APP.title };
  let attention: Json = { running: { id: id("a"), route: "app", title: APP.title }, questions: [question] };
  await page.route("**/api/overview*", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...((await response.json()) as Json), repair: attention } });
  });
  const state = { listing: { running: attention.running, engine: "Claude", repairs: ROWS } as Json,
    detail: { [id("a")]: running({ status: "waiting", timeline: [...STEPS, QUESTION] }) } as Record<string, Json>,
    post: async (_path: string, body: Json) => {
      attention = { ...attention, questions: [{ ...question, answer: body.answer }].filter((item) => item.answer === null) };
      return { status: 200, json: state.detail[id("a")]! };
    } };
  await repairs(page, state);

  const section = page.getByRole("region", { name: "Altitude" });
  const card = section.getByRole("article", { name: "Go ahead?" });
  await walk.open("/");
  await walk.state("01-needs-you-question", {
    visible: [section.getByRole("heading", { name: "Altitude" }), card.getByText("Asked by the repair"), card.getByRole("link", { name: APP.title }),
      card.getByText(QUESTION.command), card.getByRole("button", { name: "Go ahead" }), card.getByRole("button", { name: "Don’t" })],
    hidden: [page.getByText("Nothing needs you.")],
  });
  // The question counts in the Needs you badge, and answering it takes it out.
  const primary = page.getByRole("navigation", { name: info.project.name === "phone" ? "Primary" : "Rail", exact: true });
  const badge = primary.getByRole("link", { name: /Needs you/ }).locator(".badge");
  await expect(badge).toBeVisible();
  const before = Number(await badge.textContent());
  await card.getByRole("button", { name: "Don’t" }).click();
  await walk.state("02-needs-you-answered", { visible: [], hidden: [section] });
  if (before > 1) await expect(badge).toHaveText(String(before - 1));
  else await expect(badge).toBeHidden();

  attention = { running: null, questions: [{ ...question, answer: "expired" }] };
  await page.reload();
  await walk.state("03-needs-you-expired", { visible: [card.getByText("Expired · the session ended")], hidden: [card.getByRole("button", { name: "Go ahead" })] });

  // Monitor lists Altitude's own sessions; a repair shows in Work, not here.
  await page.goto("/monitor");
  await walk.state("04-monitor-without-repair", {
    visible: [page.locator(".monitor-session").first()],
    hidden: [page.locator(".monitor-session").filter({ hasText: /REPAIR/i }), page.getByRole("link", { name: APP.title })],
  });
});
