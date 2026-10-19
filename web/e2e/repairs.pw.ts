import { expect, type Page, type Route } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Repairs (SPEC.md §3.15, docs/OPERATIONS.md#repairs), walked at both widths: Settings → About's Repair rows and the
 * full list, the start sheet with its refusals, a repair's page while it works, asks and runs low on time, how it ends
 * (Stop, the time limit, a report with Altitude's check, no report), a terminal repair's page, and a repair's question
 * in Needs you and its row in Monitor. The service is real; the repairs are fictional fixtures behind routes, so no
 * agent runs and nothing gets full access.
 */

type Json = Record<string, unknown>;
const id = (digit: string) => digit.repeat(32);

// An overview or monitor read still in flight at teardown would find its response disposed.
test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "wait" }); });
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const APP = { id: id("a"), title: "The page does not load on my phone", route: "app", engine: "claude", started: ago(12), ended: null, outcome: null, status: "working" };
const ROWS = [
  APP,
  { id: id("b"), title: "Failed update to 0.9.0", route: "app", engine: "claude", started: "2026-10-18T08:00:00Z", ended: "2026-10-18T08:20:00Z", outcome: "repaired", status: "repaired" },
  { id: id("c"), title: "Tasks stopped starting after I moved the projects folder somewhere else entirely", route: "terminal", engine: "claude", started: "2026-10-17T08:00:00Z", ended: "2026-10-17T08:30:00Z", outcome: "changed_by_hand", status: "changed_by_hand" },
  { id: id("d"), title: "Repair", route: "terminal", engine: "claude", started: "2026-10-16T08:00:00Z", ended: "2026-10-16T08:05:00Z", outcome: "no_report", status: "no_report" },
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

test("Settings → About: Repair…, the start sheet, its refusals, recent repairs and every repair", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const about = page.getByRole("region", { name: "About" });
  const show = () => about.scrollIntoViewIfNeeded();
  const intro = about.getByText("Something not working? A repair agent finds out why and fixes it, with full access while it works.");
  const start = about.getByRole("button", { name: "Repair…" });
  const loading = page.getByText("Loading repairs…");
  const failed = page.getByRole("alert").filter({ hasText: "Could not load repairs." });
  const allRepairs = page.getByRole("link", { name: /All repairs/ });
  const row = (title: string | RegExp) => page.getByRole("link", { name: title });

  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  let listing: Json | null = null;
  const state = { listing: { running: null, engine: "Claude", repairs: [] } as Json, detail: {} as Record<string, Json>,
    post: async (_path: string, _body: Json) => ({ status: 200, json: running() }) };
  const posts = await repairs(page, state);
  await page.route("**/api/repairs", async (route) => {
    if (route.request().method() === "POST") return route.fallback();
    if (listing) return route.fulfill({ json: listing });
    await held;
    return route.fulfill({ status: 500, json: { error: "fixture" } });
  });

  await walk.open("/settings");
  await walk.state("01-loading", { action: show, visible: [intro, start, loading], hidden: [failed] });
  release();
  await expect(failed).toBeVisible({ timeout: 20_000 });
  await walk.state("02-load-failed", { action: show, visible: [intro, failed, failed.getByRole("button", { name: "Retry" })], hidden: [loading] });

  listing = { running: null, engine: "Claude", repairs: [] };
  await failed.getByRole("button", { name: "Retry" }).click();
  await walk.state("03-no-repairs", { action: show, visible: [intro, start], hidden: [failed, loading, allRepairs] });

  // The sheet: what full access means, the field, the plan line; Start waits for words.
  await start.click();
  const sheet = page.getByRole("dialog", { name: "Start a repair" });
  const field = sheet.getByRole("textbox", { name: "What’s wrong?" });
  const go = sheet.getByRole("button", { name: /Start repair/ });
  await walk.state("04-sheet-empty", {
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
  await walk.state("05-sheet-already-running", { visible: [refused.getByText("A repair is already running."), refused.getByRole("link", { name: "Open it" }), go], hidden: [] });
  refusal = { status: 409, json: { error: "No coding agent can run now: Claude's weekly allowance is used. Run `alt repair` in a terminal instead." } };
  await go.click();
  await walk.state("06-sheet-no-agent", {
    visible: [refused.getByText(/weekly allowance is used/), refused.locator("code", { hasText: "alt repair" })],
    hidden: [refused.getByRole("link", { name: "Open it" })],
  });

  // Starting holds Start repair as Starting…; once accepted the page opens, and the same request identity was sent each time.
  let accept: () => void = () => {};
  const accepted = new Promise<void>((resolve) => { accept = resolve; });
  state.detail[id("a")] = running({ timeline: STEPS.slice(0, 2) });
  state.post = async () => { await accepted; return { status: 200, json: state.detail[id("a")]! }; };
  await go.click();
  await walk.state("07-sheet-starting", { visible: [sheet.getByRole("button", { name: "Start repair, starting" }), sheet.getByText("Starting…")], hidden: [refused] });
  await expect(sheet.getByRole("button", { name: "Cancel" })).toBeDisabled();
  accept();
  await expect(page).toHaveURL(new RegExp(`/repairs/${id("a")}$`));
  const requests = new Set(posts.filter((post) => post.path === "/api/repairs").map((post) => post.body.request));
  expect(requests.size, "one sheet sends one request identity").toBe(1);
  expect(posts.at(-1)!.body.problem).toContain("It worked yesterday.");

  // Back in Settings: the running repair replaces Repair…; recent repairs, their chips, and All repairs.
  listing = { running: { id: id("a"), route: "app", title: APP.title }, engine: "Claude", repairs: ROWS };
  await page.goto("/settings");
  const runningRow = row(/The page does not load on my phone/);
  const repaired = row(/Failed update to 0\.9\.0/);
  const byHand = row(/Tasks stopped starting/);
  await walk.state("08-running-and-recent", {
    action: show,
    visible: [intro, about.getByText("Running ·"), about.getByRole("link", { name: "Open", exact: true }), runningRow.getByText("Working"),
      repaired.getByText("✓ Repaired"), byHand.getByText("Confirm yourself"), byHand.getByText("From a terminal"), allRepairs.getByText("4 repairs")],
    hidden: [start, failed, row(/Oct 16 · Repair/)],
  });
  const title = byHand.locator(".repair-row-title");
  expect(await title.evaluate((node) => node.scrollWidth > node.clientWidth), "a long title stays on one line").toBe(true);
  await expect(about.getByRole("link", { name: "Open", exact: true })).toHaveAttribute("href", `/repairs/${id("a")}`);

  listing = { running: { id: null, route: "terminal" }, engine: "Claude", repairs: ROWS.slice(1) };
  await page.reload();
  await walk.state("09-running-in-terminal", { action: show, visible: [about.getByText("Running in a terminal")], hidden: [start, about.getByRole("link", { name: "Open", exact: true })] });

  listing = { running: null, engine: "Claude", repairs: ROWS.slice(1).concat({ ...ROWS[3]!, id: id("e"), status: "stopped", outcome: null }) };
  await page.reload();
  await allRepairs.click();
  await expect(page).toHaveURL(/\/settings\/repairs$/);
  await walk.state("10-all-repairs", {
    visible: [page.getByRole("heading", { name: "Repairs" }), repaired, byHand, row(/Oct 16 · Repair/).first().getByText("✕ Not repaired"), page.getByText("Stopped", { exact: true })],
    hidden: [intro],
  });
  listing = { running: null, engine: null, repairs: [] };
  await page.reload();
  await walk.state("11-all-repairs-empty", { visible: [page.getByText("No repairs yet. Start one with Repair… in Settings → About.")], hidden: [repaired] });
});

test("a repair's page while it works: steps, messages, questions, your step and the time limit", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const state = { listing: { running: { id: id("a"), route: "app", title: APP.title }, engine: "Claude", repairs: ROWS } as Json,
    detail: { [id("a")]: running() } as Record<string, Json>,
    post: async (path: string, body: Json) => ({ status: 200, json: state.detail[id("a")]! }) };
  const posts = await repairs(page, state);
  const heading = page.getByRole("heading", { name: `Repair · ${APP.title}` });
  const stop = page.getByRole("button", { name: "Stop", exact: true });
  const composer = page.getByRole("textbox", { name: "Message the repair" });
  const access = page.locator(".repair-access").first();
  const step = (lead: string) => page.locator(".repair-step").filter({ hasText: lead });

  await walk.open(`/repairs/${id("a")}`);
  await walk.state("01-working", {
    visible: [heading, page.locator(".repair-head-meta").getByText("Working"), page.getByText(/^Claude .* · 12 min$/), stop,
      access.getByText("Full access · 1 h 48 min left"),
      page.getByText("Started by you · full access for 2 hours"), page.getByText("The page doesn't load on my phone since this morning."),
      page.getByText("I'll check the service and its address first."),
      step("Ran:"), step("Failed (exit 2):"), step("Changed:"), step("Fetched:"), step("Running:").locator(".spinner"),
      page.getByText("Altitude is restarting · the repair continues"), page.getByText("Altitude is back"), composer],
    hidden: [page.getByRole("button", { name: "Keep going" }), page.getByText("Session ended"), page.getByRole("tab")],
  });
  await expect(page.getByRole("navigation", { name: "Primary" }).locator("[aria-current=page]")).toHaveCount(0);
  const ran = step("Ran:");
  await ran.getByRole("button", { name: "Show" }).click();
  await walk.state("02-step-shown", { visible: [ran.getByText("active (running)"), ran.getByRole("button", { name: "Hide" })], hidden: [] });
  const box = await page.locator(".repair-step").filter({ hasText: "Failed" }).getByRole("button", { name: "Show" }).boundingBox();
  expect(box!.height, "Show has a 44px tap area").toBeGreaterThanOrEqual(44);

  // A message joins the timeline as yours and the agent reads it at its next turn.
  state.post = async (path, body) => {
    if (path.endsWith("/message")) state.detail[id("a")] = running({ timeline: [...STEPS, { kind: "you", at: ago(0), text: body.text }] });
    return { status: 200, json: state.detail[id("a")]! };
  };
  await composer.fill("The phone is on the home Wi-Fi.");
  await composer.press("Enter");
  await walk.state("03-message-sent", { visible: [page.locator(".repair-you").getByText("The phone is on the home Wi-Fi.")], hidden: [] });
  await expect(composer).toHaveValue("");
  expect(posts.filter((post) => post.path.endsWith("/message"))).toHaveLength(1);

  // The repair asks before a big change; Go ahead settles it.
  state.detail[id("a")] = running({ status: "waiting", timeline: [...STEPS, QUESTION] });
  await page.reload();
  const card = page.getByRole("article", { name: "Go ahead?" });
  await walk.state("04-question", {
    visible: [page.locator(".repair-head-meta").getByText("Waiting for you"), card.getByText("Asked by the repair"), card.getByText(QUESTION.reason),
      card.getByText(QUESTION.command), card.getByRole("button", { name: "Go ahead" }), card.getByRole("button", { name: "Don’t" })],
    hidden: [card.getByText("Went ahead · by you")],
  });
  state.post = async (path, body) => {
    state.detail[id("a")] = running({ timeline: [...STEPS, { ...QUESTION, answer: body.answer, answered: ago(0) }] });
    return { status: 200, json: state.detail[id("a")]! };
  };
  await card.getByRole("button", { name: "Go ahead" }).click();
  await walk.state("05-went-ahead", { visible: [card.getByText("Went ahead · by you")], hidden: [card.getByRole("button", { name: "Go ahead" }), card.getByRole("button", { name: "Don’t" })] });
  expect(posts.at(-1)).toMatchObject({ path: `/api/repairs/${id("a")}/answer`, body: { question: "q1", answer: "yes" } });

  // A step that needs the operator's password: Copy and I ran it.
  state.detail[id("a")] = running({ status: "waiting", timeline: [...STEPS, YOUR_STEP] });
  await page.reload();
  const yours = page.getByRole("article", { name: YOUR_STEP.title });
  await walk.state("06-your-step", {
    visible: [yours.getByText(YOUR_STEP.command), yours.getByRole("button", { name: "Copy" }), yours.getByRole("button", { name: "I ran it" })],
    hidden: [yours.getByRole("button", { name: "Go ahead" })],
  });
  state.post = async (_path, body) => {
    state.detail[id("a")] = running({ timeline: [...STEPS, { ...YOUR_STEP, answer: body.answer, answered: ago(0) }] });
    return { status: 200, json: state.detail[id("a")]! };
  };
  await yours.getByRole("button", { name: "I ran it" }).click();
  await walk.state("07-you-ran-it", { visible: [yours.getByText("Done · you ran it")], hidden: [yours.getByRole("button", { name: "I ran it" })] });

  // Fifteen minutes before the end Keep going appears; it adds an hour.
  state.detail[id("a")] = running({ left: 14 * 60, keep_going: true });
  await page.reload();
  const keep = page.getByRole("button", { name: "Keep going" });
  await walk.state("08-time-low", { visible: [access.getByText(/Full access · 14 min left ·/), keep], hidden: [] });
  state.post = async () => {
    state.detail[id("a")] = running({ left: 74 * 60, timeline: [...STEPS, { kind: "event", at: ago(0), event: "kept_going" }] });
    return { status: 200, json: state.detail[id("a")]! };
  };
  await keep.click();
  await walk.state("09-kept-going", { visible: [access.getByText("Full access · 1 h 14 min left"), page.getByText("Kept going · one more hour")], hidden: [keep] });
  expect(posts.filter((post) => post.path.endsWith("/keep-going"))).toHaveLength(1);

  // Altitude stops answering: after the read's retries the page keeps what it has and says so.
  await page.route(/\/api\/repairs\/[^/]+$/, (route) => route.request().method() === "GET" ? route.abort() : route.fallback());
  const silent = page.getByText("Altitude is not answering · the repair continues and this page catches up when it is back");
  await expect(silent).toBeVisible({ timeout: 30_000 });
  await walk.state("10-not-answering", {
    visible: [silent, access.getByText(/Full access · 1 h 14 min left/)],
    hidden: [],
  });
});

test("how a repair ends: Stop, the time limit, its report and Altitude's check, no report, and a terminal repair", async ({ page }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const state = { listing: { running: { id: id("a"), route: "app", title: APP.title }, engine: "Claude", repairs: ROWS } as Json,
    detail: { [id("a")]: running({ timeline: [...STEPS, QUESTION] }) } as Record<string, Json>,
    post: async (_path: string, _body: Json) => ({ status: 200, json: state.detail[id("a")]! }) };
  await repairs(page, state);
  const stop = page.getByRole("button", { name: "Stop", exact: true });
  const composer = page.getByRole("textbox", { name: "Message the repair" });
  const access = page.locator(".repair-access").first();
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
    visible: [page.locator(".repair-head-meta").getByText("Stopped"), access.getByText("Session ended"),
      page.getByText("Stopped by you · the agent and its processes were stopped"), page.getByText(/^Cut off by the end of the session:/),
      card.getByText("Expired · the session ended"), page.getByRole("heading", { name: "Left running" }), page.getByText("altitude-tunnel.service")],
    hidden: [stop, composer, card.getByRole("button", { name: "Go ahead" }), continueRepair, page.getByText("The repair ended without a report")],
  });

  state.detail[id("a")] = ended("stop", { status: "stopped", stop: { at: ago(0), update: true }, left_running: [] });
  await page.reload();
  await walk.state("04-stopped-during-update", { visible: [page.getByText("Stopped by you · the update it started continues; its result appears here")], hidden: [page.getByText("Left running")] });

  state.detail[id("a")] = running({ stop: { at: ago(0), update: false }, stop_unconfirmed: true });
  await page.reload();
  await walk.state("05-stop-unconfirmed", { visible: [page.getByText("Stop unconfirmed · The repair may still be running."), page.getByRole("button", { name: "Check status" })], hidden: [composer] });

  // The time ran out: Keep going resumes the same session for an hour, or says why it could not.
  state.detail[id("a")] = ended("time", { status: "checking", keep_going: true });
  await page.reload();
  const keep = page.getByRole("button", { name: "Keep going" });
  await walk.state("06-time-up", { visible: [access.getByText(/Time’s up · the session ended ·/), keep], hidden: [composer, stop, page.getByText("The repair ended without a report")] });
  state.post = async () => ({ status: 409, json: { error: "Couldn’t resume: No coding agent can run now: Claude's weekly allowance is used." } });
  await keep.click();
  await walk.state("07-keep-going-failed", { visible: [access.getByText(/Couldn’t resume: No coding agent can run now/)], hidden: [] });
  await expect(access.locator(".text-danger")).toBeVisible();

  // The report, then Altitude's check: Repaired, Confirm yourself, Not repaired.
  state.detail[id("a")] = ended("report", { status: "checking", report: REPORT, left_running: [] });
  await page.reload();
  await walk.state("08-report-checking", {
    visible: [page.locator(".repair-head-meta").getByText("Checking"), page.getByText("Altitude is checking the result…"), page.getByText(REPORT.wrong), continueRepair],
    hidden: [composer, stop, page.getByText(/^Altitude checked:/)],
  });
  state.detail[id("a")] = ended("report", { status: "repaired", outcome: "repaired", report: REPORT, check: CHECK, left_running: [] });
  await page.reload();
  const outcome = page.getByRole("region", { name: "Report" });
  await walk.state("09-repaired", {
    visible: [page.locator(".repair-head-meta").getByText("✓ Repaired"), outcome.getByRole("heading", { name: "Repaired", exact: true }), outcome.getByText("✓"),
      outcome.getByText("What was wrong"), outcome.getByText(REPORT.wrong), outcome.getByText("What changed"), outcome.getByText("What remains"),
      outcome.getByText("Altitude checked: version 0.9.0 · matches its release · service running · all checks pass"), outcome.getByText("Nothing new"), continueRepair],
    hidden: [outcome.getByText("Try:"), composer],
  });
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
    visible: [page.getByText(/^Started from a terminal · /), page.getByText(/^Ended · /), page.getByText("Its steps are in that terminal’s history."),
      page.getByRole("heading", { name: "Confirm yourself · the installation was changed by hand" }), page.getByText("its files differ from the release"),
      page.getByText("Brief", { exact: true }), page.getByRole("link", { name: "All repairs" })],
    hidden: [stop, page.getByText(/Full access/), page.getByRole("textbox", { name: "Message the repair" })],
  });
  await page.goto(`/repairs/${id("f")}`);
  await walk.state("14-missing-repair", { visible: [page.getByText("That repair is not on this computer.")], hidden: [page.getByRole("alert")] });
});

test("a repair's question in Needs you and its session in Monitor", async ({ page }, info) => {
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
  await page.route("**/api/monitor*", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as Json;
    await route.fulfill({ response, json: { ...body, sessions: [{ kind: "repair", id: id("a"), title: APP.title, engine: "claude", model: "claude-opus-5-5",
      state: "working", at: ago(12) }, ...(body.sessions as Json[])] } });
  });

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

  await page.goto("/monitor");
  const repairRow = page.locator(".monitor-session").filter({ hasText: "REPAIR" });
  await walk.state("04-monitor-row", {
    visible: [repairRow.getByRole("link", { name: APP.title }), repairRow.getByText(/full access · working/)],
    hidden: [],
  });
  await repairRow.getByRole("link", { name: APP.title }).click();
  await expect(page).toHaveURL(new RegExp(`/repairs/${id("a")}$`));
});
