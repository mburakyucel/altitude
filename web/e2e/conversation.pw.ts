import { test } from "./fixtures";
import { expect, type Locator, type Page, type Route, type TestInfo } from "@playwright/test";
import { fixtureProject, fixtureTask } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/*
 * The project conversation (SPEC.md §3.3), its system lines (§3.4, §4.1), and the one composer (§3.6),
 * every state walked at 390 and 1440. Real chat rows carry the loaded conversation, bubbles, prose, day
 * dividers, the folded and grouped system lines, and the expanded card. States the live service cannot
 * be asked to produce on demand (a turn in progress, a failed turn, an FYI, an empty or failed read, a
 * report prompt in the new label/value shape, a streamed or refused send, the queue, the voice states)
 * are overlaid on the real rows with page.route, named "-overlay". Every POST /api/chat, /api/chat/remove,
 * /api/l3/engine, and /api/transcribe is intercepted: nothing here sends a message to L3, changes the
 * engine pin, or uploads audio.
 */

type Row = Record<string, unknown> & { role: string; text: string; trigger?: string; turn_id?: string; at?: string };
type ChatView = Record<string, unknown> & { history: Row[]; active: unknown; busy: boolean; queued?: unknown[]; engine?: string | null };

const now = () => new Date().toISOString();
const chatApi = (name: string) => `/api/chat/${name}`;

test.afterEach(async ({ page }) => {
  // Send/pin invalidations can still be reading an overlay when the browser context is disposed.
  await page.unrouteAll({ behavior: "wait" });
});

function reportPrompt(slug: string): string {
  return [
    `Report landed for ${slug}.`,
    `Task: ${slug}`,
    "Verdict: done",
    "Problems: none",
    "Post-mortem signals: none",
    "PRs: #206 merged",
    "Spend: 3 turns, 1 review",
    "",
    `Read the full report with \`alt task report ${slug}\`. Handle the report: write a concise digest and use \`alt task done\`.`,
  ].join("\n");
}

/** Serve the real chat view patched by `patch`; the patch runs on every poll so it can read mutable state. */
async function overlayChat(page: Page, name: string, patch: (view: ChatView) => ChatView) {
  await page.route((url) => url.pathname === chatApi(name), async (route) => {
    const response = await route.fetch();
    const view = (await response.json()) as ChatView;
    await route.fulfill({ response, json: patch(view) });
  });
}

async function clearRoutes(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
}

/** The real chat rows and the slug of the latest landed report among them. */
async function liveChat(page: Page, name: string) {
  const response = await page.request.get(`${chatApi(name)}?limit=200`);
  expect(response.ok(), "Live chat must be available").toBe(true);
  const view = (await response.json()) as ChatView;
  const landed = [...view.history].reverse().find((row) => row.role === "user" && row.trigger === "report-landed");
  const slug = landed ? /Report landed for `?([A-Za-z0-9][A-Za-z0-9_.-]*)`?/.exec(landed.text)?.[1]?.replace(/\.$/, "") : undefined;
  return { view, slug };
}

function views(page: Page, info: TestInfo) {
  const phone = info.project.name === "phone";
  const main = page.getByRole("main");
  const convo = main.getByRole("region", { name: "Conversation", exact: true });
  return {
    phone,
    main,
    convo,
    field: main.getByRole("textbox", { name: /^Message L3 about /, includeHidden: true }),
    send: main.getByRole("button", { name: "Send", exact: true }),
    queue: main.getByRole("button", { name: "Queue", exact: true }),
    mic: main.getByRole("button", { name: "Start voice input", exact: true }),
    stop: main.getByRole("button", { name: "Stop voice input", exact: true }),
    cancel: main.getByRole("button", { name: "Cancel voice input", exact: true }),
    hint: main.locator(".composer-hint"),
    pill: main.getByRole("combobox", { name: "L3 engine", exact: true }),
    loading: convo.getByLabel("Loading", { exact: true }),
    lines: convo.locator(".sys-line"),
    bubble: (text: string) => convo.locator(".bubble", { hasText: text }),
    status: main.locator(phone ? ".phone-status" : ".project-header p[aria-live]"),
  };
}

/** Show the row's time: hover on a desktop, a long press on the phone (SPEC.md §3.3). */
async function revealTime(row: Locator, phone: boolean) {
  if (!phone) {
    await row.hover();
    return;
  }
  await row.dispatchEvent("pointerdown", { pointerType: "touch", bubbles: true });
  await row.page().waitForTimeout(650);
  await row.dispatchEvent("pointerup", { pointerType: "touch", bubbles: true });
}

test("real rows: bubbles, prose, day dividers, the time in the gutter, folded and grouped system lines, the card", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const { view } = await liveChat(page, project.name);
  const runs = view.history.filter((row) => row.role === "user").reduce<number[]>((runs, row) => {
    const system = Boolean(row.trigger && row.trigger !== "chat");
    if (system) runs[runs.length - 1] = (runs[runs.length - 1] ?? 0) + 1;
    else if (runs[runs.length - 1]) runs.push(0);
    return runs;
  }, [0]);
  expect(runs.some((n) => n >= 2), "The walkthrough needs a run of two or more system turns in the live chat").toBe(true);
  expect(runs.some((n) => n === 1), "The walkthrough needs one lone system turn in the live chat").toBe(true);
  // Keep the real rows stable while live turns arrive during the walkthrough.
  await overlayChat(page, project.name, () => ({ ...view, active: null, busy: false }));

  await walk.open(project.path);
  const lastRow = v.convo.locator(".msg-row").last();
  await walk.state("01-loaded", {
    visible: [v.convo, v.convo.locator(".bubble").first(), v.convo.locator(".reply").first(), v.convo.getByRole("separator").first(), v.lines.first(), v.field, v.send, v.mic, ...(!v.phone ? [v.pill] : [])],
    hidden: [v.loading, v.main.getByText("Say what you want done."), v.queue, ...(v.phone ? [v.pill, v.hint] : [])],
  });
  await expect(v.send).toBeDisabled();
  await expect(v.send).toHaveText("");
  await expect(v.send.locator("svg")).toBeVisible();
  await expect(v.hint).toHaveText("L3 answers or creates one task. Shift + Enter for a new line.");
  await expect(v.status).toContainText(v.phone ? "L3 · Ready" : /L3 answered .* on /);

  await expect(lastRow.locator(".msg-time")).toHaveCSS("opacity", "0");
  await walk.state("02-time-in-the-gutter", {
    action: () => revealTime(lastRow, v.phone),
    visible: [lastRow.locator(".msg-time")],
    hidden: [],
  });
  await expect(lastRow.locator(".msg-time")).toHaveCSS("opacity", "1");

  const groups = v.convo.locator(".sys-line[data-group]");
  const group = groups.first();
  const groupCount = await groups.count();
  await expect(group.locator(".sys-text")).toHaveText(/^L3 handled \d+ system events between your messages$/);
  const single = v.convo.locator(".sys-line[data-turn]").first();
  await expect(single.getByRole("button", { name: "Show", exact: true })).toBeVisible();
  const list = v.convo.getByRole("group", { name: /system events$/ });
  await walk.state("03-group-expanded", {
    action: () => group.getByRole("button", { name: "Show", exact: true }).click(),
    visible: [list, list.locator(".sys-line[data-turn]").first()],
    hidden: [],
  });
  await expect(groups).toHaveCount(groupCount - 1);
  expect(await list.locator(".sys-line[data-turn]").count()).toBeGreaterThanOrEqual(2);
  await expect(list.locator(".sys-line[data-turn] .sys-text").first()).toContainText(/^\d{1,2}:\d\d( [AP]M)? · /);

  const article = v.convo.getByRole("article");
  await walk.state("04-card-expanded", {
    action: async () => {
      await list.getByRole("button", { name: "Show", exact: true }).first().click();
      await article.scrollIntoViewIfNeeded();
    },
    visible: [article, article.getByText("What altd sent L3"), article.getByText("L3 replied"), article.getByRole("button", { name: "Hide", exact: true })],
    hidden: [],
  });
  await walk.state("05-card-hidden", {
    action: () => article.getByRole("button", { name: "Hide", exact: true }).click(),
    visible: [list],
    hidden: [article],
  });
  await walk.state("06-group-folded", {
    action: () => list.getByRole("button", { name: "Hide", exact: true }).click(),
    visible: [group],
    hidden: [list],
  });
  await expect(groups).toHaveCount(groupCount);
});

test("a landed report in the label/value shape, its card links, and the report view (overlay on a real task)", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const { view, slug } = await liveChat(page, project.name);
  expect(slug, "The walkthrough needs one landed report in the live chat").toBeTruthy();
  const chat = view.history.filter((row) => row.trigger === "chat").slice(-2);
  const rows: Row[] = [
    ...chat,
    { at: now(), role: "user", text: reportPrompt(slug!), trigger: "report-landed", turn_id: "ui-s1" },
    { at: now(), role: "assistant", text: "Read the report and the digest.\n\nClosed it as done.", trigger: "report-landed", turn_id: "ui-s1" },
  ];
  await overlayChat(page, project.name, (live) => ({ ...live, history: rows, active: null, busy: false, queued: [] }));

  await walk.open(project.path);
  const line = v.lines.first();
  await walk.state("01-folded-overlay", {
    visible: [line.getByText("Closed it as done."), line.getByRole("button", { name: "Show", exact: true })],
    hidden: [v.convo.getByRole("article")],
  });
  const article = v.convo.getByRole("article");
  await walk.state("02-card-label-value-rows-overlay", {
    action: async () => {
      await line.getByRole("button", { name: "Show", exact: true }).click();
      await article.scrollIntoViewIfNeeded();
    },
    visible: [
      article.getByText(/^Report landed · /),
      article.locator("dt", { hasText: "Verdict" }),
      article.locator("dd", { hasText: "#206 merged" }),
      article.getByText("Closed it as done."),
      article.getByRole("link", { name: "Open task", exact: true }),
      article.getByRole("link", { name: "Full report", exact: true }),
    ],
    hidden: [article.locator("pre"), v.lines],
  });
  await walk.state("03-report-view", {
    action: () => article.getByRole("link", { name: "Full report", exact: true }).click(),
    visible: [v.main.getByRole("heading", { level: 1, name: "Report", exact: true })],
    hidden: [v.convo],
  });
});

test("a turn in progress, a failed turn, an FYI, and an empty conversation (overlays)", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const { view, slug } = await liveChat(page, project.name);
  const chat = view.history.filter((row) => row.trigger === "chat").slice(-2);

  await overlayChat(page, project.name, (live) => ({
    ...live,
    history: [...chat, { at: now(), role: "user", text: reportPrompt(slug ?? "task"), trigger: "report-landed", turn_id: "ui-s2" }],
    active: { id: "ui-s2", started_at: now(), trigger: "report-landed" },
    busy: true,
    queued: [],
  }));
  await walk.open(project.path);
  const progress = v.lines.first();
  await walk.state("01-in-progress-overlay", {
    visible: [progress.getByText(/^L3 is handling a landed report for /), v.queue, ...(!v.phone ? [v.hint] : [])],
    hidden: [progress.getByRole("button", { name: "Show", exact: true }), v.send, ...(v.phone ? [v.hint] : [])],
  });
  await expect(v.status).toContainText(v.phone ? /^L3 · Handling a landed report/ : /^L3 is handling a landed report/);

  await clearRoutes(page);
  await overlayChat(page, project.name, (live) => ({
    ...live,
    history: [
      ...chat,
      { at: now(), role: "user", text: `Recover the session for ${project.name}/${slug ?? "task"}`, trigger: "system-recovery", turn_id: "ui-s3" },
      { at: now(), role: "error", text: "L3 turn failed: the engine timed out", trigger: "system-recovery", turn_id: "ui-s3" },
      { at: now(), role: "user", text: "Anything else?", trigger: "chat", turn_id: "ui-s4" },
      { at: now(), role: "assistant", text: "Nothing waits on you.", trigger: "chat", turn_id: "ui-s4" },
      { at: now(), role: "system", text: "The nightly build is green again.", trigger: "fyi" },
    ],
    active: null,
    busy: false,
    queued: [],
  }));
  await walk.open(project.path);
  const failed = v.lines.filter({ hasText: /^L3 could not handle a recovery/ });
  const fyi = v.lines.filter({ hasText: "The nightly build is green again." });
  await walk.state("02-failed-turn-and-fyi-overlay", {
    visible: [failed, fyi, fyi.getByRole("button", { name: "Show", exact: true })],
    hidden: [v.convo.getByRole("article")],
  });
  await expect(failed.locator(".sys-dot")).toHaveAttribute("data-tone", "danger");
  const article = v.convo.getByRole("article");
  await walk.state("03-failed-turn-shown-overlay", {
    action: async () => {
      await failed.getByRole("button", { name: "Show", exact: true }).click();
      await article.scrollIntoViewIfNeeded();
    },
    visible: [article.getByText(/^Recovery · /), article.getByText("L3 could not answer"), article.getByText("L3 turn failed: the engine timed out")],
    hidden: [failed],
  });

  await clearRoutes(page);
  await overlayChat(page, project.name, (live) => ({ ...live, history: [], active: null, busy: false, queued: [] }));
  await walk.open(project.path);
  await walk.state("04-empty-overlay", {
    visible: [v.convo.getByText("Say what you want done. L3 answers or creates one task."), v.field],
    hidden: [v.convo.locator(".bubble"), v.lines],
  });
});

test("loading, then a failed read with Retry and the cached rows", async ({ page, request }, info) => {
  test.setTimeout(120_000);
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const isChat = (url: URL) => url.pathname === chatApi(project.name);

  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route(isChat, async (route) => {
    await gate;
    await route.continue();
  });
  await walk.open(project.path);
  await walk.state("01-loading", { visible: [v.loading], hidden: [v.convo.locator(".bubble")] });
  expect(await v.loading.locator(".skeleton").count()).toBe(3);
  release();
  await walk.state("02-loaded", { visible: [v.convo.locator(".bubble").first()], hidden: [v.loading] });

  await clearRoutes(page);
  let fail = true;
  await page.route(isChat, (route) => (fail ? route.fulfill({ status: 500, json: { error: "boom" } }) : route.continue()));
  await walk.open(project.path);
  const error = v.convo.getByText(/^Could not load the conversation\./);
  await walk.state("03-error-retry", {
    action: () => error.waitFor({ timeout: 30_000 }),
    visible: [error, v.convo.getByRole("button", { name: "Retry", exact: true })],
    hidden: [v.convo.locator(".bubble"), v.loading],
  });
  fail = false;
  await walk.state("04-recovered", {
    action: () => v.convo.getByRole("button", { name: "Retry", exact: true }).click(),
    visible: [v.convo.locator(".bubble").first()],
    hidden: [error],
  });
});

test("send: the bubble at 60%, the streamed reply, one conversation after the poll; a refused send; a failed reply's Retry", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const text = "UI walkthrough: this message never reaches L3";
  const stored: Row[] = [];
  let mode: "refuse" | "stream" = "refuse";
  let releaseStream: () => void = () => {};
  const streamGate = new Promise<void>((resolve) => (releaseStream = resolve));
  const posts: string[] = [];
  await page.route((url) => url.pathname === "/api/chat", async (route: Route) => {
    posts.push((route.request().postDataJSON() as { text: string }).text);
    if (mode === "refuse") return route.fulfill({ status: 500, json: { error: "no L3 for this project" } });
    await streamGate;
    stored.push(
      { at: now(), role: "user", text, trigger: "chat", turn_id: "ui-c1" },
      { at: now(), role: "assistant", text: "Noted. Nothing to do for that.", trigger: "chat", turn_id: "ui-c1" },
    );
    const lines = [
      JSON.stringify({ turn: { id: "ui-c1", started_at: now(), trigger: "chat" } }),
      JSON.stringify({ t: "Noted. " }),
      JSON.stringify({ t: "Nothing to do for that." }),
      JSON.stringify({ done: { turn_id: "ui-c1" } }),
    ];
    return route.fulfill({ status: 200, contentType: "application/x-ndjson", body: `${lines.join("\n")}\n` });
  });
  await overlayChat(page, project.name, (live) => ({ ...live, history: [...live.history, ...stored], active: null, busy: false, queued: [] }));

  await walk.open(project.path);
  const alert = v.main.getByRole("alert").filter({ hasText: "Not sent." });
  await walk.state("01-typing", {
    action: () => v.field.fill(text),
    visible: [v.send],
    hidden: [alert],
  });
  await expect(v.send).toBeEnabled();
  await expect(v.send).toHaveText("");
  await expect(v.send.locator("svg")).toBeVisible();
  await walk.state("02-refused-not-sent-retry", {
    action: () => v.send.click(),
    visible: [alert, alert.getByRole("button", { name: "Retry", exact: true })],
    hidden: [v.bubble(text)],
  });
  await expect(v.field).toHaveValue(text);
  await expect(alert).toHaveClass(/text-danger/);

  mode = "stream";
  const pending = v.convo.locator(".msg-row[data-pending]");
  await walk.state("03-sending-bubble-at-60", {
    action: () => alert.getByRole("button", { name: "Retry", exact: true }).click(),
    visible: [pending, v.bubble(text)],
    hidden: [alert],
  });
  await expect(v.field).toHaveValue("");
  await expect(pending).toHaveCSS("opacity", "0.6");
  releaseStream();
  await walk.state("04-reply-streamed", {
    visible: [v.convo.locator(".reply", { hasText: "Nothing to do for that." })],
    hidden: [pending],
  });
  // The poll after the stream carries the server's rows for the turn: still one bubble, one reply.
  await expect(v.bubble(text)).toHaveCount(1);
  await expect(v.convo.locator(".reply", { hasText: "Nothing to do for that." })).toHaveCount(1);
  expect(posts).toEqual([text, text]);

  await clearRoutes(page);
  const retried: string[] = [];
  await page.route((url) => url.pathname === "/api/chat", (route) => {
    retried.push((route.request().postDataJSON() as { text: string }).text);
    return route.fulfill({ status: 500, json: { error: "still no L3" } });
  });
  await overlayChat(page, project.name, (live) => ({
    ...live,
    history: [
      ...live.history,
      { at: now(), role: "user", text, trigger: "chat", turn_id: "ui-c2" },
      { at: now(), role: "error", text: "L3 turn failed: the engine timed out", trigger: "chat", turn_id: "ui-c2" },
    ],
    active: null,
    busy: false,
    queued: [],
  }));
  await walk.open(project.path);
  const failed = v.convo.getByText(/^L3 could not answer this turn\./);
  await walk.state("05-failed-reply-overlay", {
    visible: [failed, failed.getByRole("button", { name: "Retry", exact: true })],
    hidden: [v.convo.getByText("L3 turn failed: the engine timed out")],
  });
  await failed.getByRole("button", { name: "Retry", exact: true }).click();
  await expect.poll(() => retried).toEqual([text]);
});

test("busy: the arrow queues, the queued row with Remove, the typing indicator, and Remove taking the row back", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const text = "UI walkthrough: queued, never run";
  const queued: Row[] = [];
  const removed: string[] = [];
  await page.route((url) => url.pathname === "/api/chat", (route) => {
    const row = { id: "ui-q1", at: now(), trigger: "chat", role: "user", text: (route.request().postDataJSON() as { text: string }).text, position: 1 };
    queued.push(row);
    return route.fulfill({ json: { queued: row } });
  });
  await page.route((url) => url.pathname === "/api/chat/remove", (route) => {
    removed.push((route.request().postDataJSON() as { id: string }).id);
    queued.length = 0;
    return route.fulfill({ json: { ok: true } });
  });
  await overlayChat(page, project.name, (live) => {
    const lastChat = [...live.history].reverse().find((row) => row.role === "user" && row.trigger === "chat");
    const id = lastChat?.turn_id ?? "ui-c9";
    const history = live.history.filter((row) => row.turn_id !== id || row.role === "user");
    return { ...live, history, active: { id, started_at: now(), trigger: "chat" }, busy: true, queued: [...queued] };
  });

  await walk.open(project.path);
  const list = v.convo.getByRole("list", { name: "Queued messages", exact: true });
  await walk.state("01-busy-typing-indicator-overlay", {
    visible: [v.convo.getByRole("status", { name: "L3 is answering", exact: true }), v.queue, ...(!v.phone ? [v.hint] : [])],
    hidden: [v.send, list, ...(v.phone ? [v.hint] : [])],
  });
  await expect(v.status).toContainText(v.phone ? "L3 · Answering" : /^L3 is answering/);
  await expect(v.queue).toBeDisabled();
  await expect(v.queue).toHaveText("");
  await expect(v.queue.locator("svg")).toBeVisible();
  await walk.state("02-busy-draft-arrow-overlay", {
    action: () => v.field.fill(text),
    visible: [v.queue, ...(!v.phone ? [v.hint] : [])],
    hidden: [list, ...(v.phone ? [v.hint] : [])],
  });
  await expect(v.queue).toBeEnabled();
  await expect(v.queue).toHaveText("");
  await expect(v.queue).toHaveCSS("width", v.phone ? "44px" : "36px");
  await expect(v.queue).toHaveCSS("height", v.phone ? "44px" : "36px");
  await expect(v.hint).toHaveText("L3 is mid-turn · runs next");
  await walk.state("03-queued-row-overlay", {
    action: () => v.queue.click(),
    visible: [list.getByText(text), list.getByRole("button", { name: "Remove", exact: true })],
    hidden: [v.bubble(text)],
  });
  await expect(v.field).toHaveValue("");
  await walk.state("04-removed-overlay", {
    action: () => list.getByRole("button", { name: "Remove", exact: true }).click(),
    visible: [v.queue],
    hidden: [list],
  });
  expect(removed).toEqual(["ui-q1"]);
});

test("the engine pin: Auto and the engines the API names; the pin posts and is read back", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const overview = await (await request.get("/api/overview")).json() as { engines: { engine: string; label: string }[] };
  expect(overview.engines.length, "The walkthrough needs one configured engine").toBeGreaterThan(0);
  const first = overview.engines[0]!;
  let pinned: string | null = null;
  const pins: (string | null)[] = [];
  await page.route((url) => url.pathname === "/api/l3/engine", (route) => {
    pinned = (route.request().postDataJSON() as { engine: string | null }).engine;
    pins.push(pinned);
    return route.fulfill({ json: { ok: true } });
  });
  await overlayChat(page, project.name, (live) => ({ ...live, engine: pinned }));

  await walk.open(project.path);
  if (v.phone) await page.getByRole("button", { name: "More actions" }).click();
  await walk.state("01-auto", { visible: [v.pill], hidden: [] });
  await expect(v.pill).toHaveValue("");
  expect(await v.pill.locator("option").allTextContents()).toEqual(["Auto", ...overview.engines.map((e) => e.label)]);
  await walk.state("02-pinned", {
    action: () => v.pill.selectOption(first.engine),
    visible: [v.pill],
    hidden: [],
  });
  await expect(v.pill).toHaveValue(first.engine);
  if (v.phone) await expect(v.status).toContainText(first.label);
  await v.pill.selectOption("");
  await expect(v.pill).toHaveValue("");
  await expect.poll(() => pins).toEqual([first.engine, null]);
});

/** A microphone that yields a tone, so the waveform has something to draw; Chromium records it as webm. */
const FAKE_MIC = `
  const context = new AudioContext();
  const oscillator = context.createOscillator();
  oscillator.frequency.value = 220;
  oscillator.start();
  Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
    configurable: true,
    // A fresh stream per call, as a real microphone gives: the composer stops the tracks it got.
    value: async () => {
      await context.resume();
      const destination = context.createMediaStreamDestination();
      oscillator.connect(destination);
      return destination.stream;
    },
  });
`;

test("voice: listening, cancelled, transcribing, landed (nothing else appears), failed", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  await page.addInitScript(FAKE_MIC);
  await overlayChat(page, project.name, (live) => ({ ...live, active: null, busy: false, queued: [] }));
  let answer: "ok" | "fail" = "ok";
  let release: () => void = () => {};
  let gate = new Promise<void>((resolve) => (release = resolve));
  const uploads: string[] = [];
  await page.route((url) => url.pathname === "/api/transcribe", async (route) => {
    uploads.push(route.request().headers()["content-type"] ?? "");
    await gate;
    return answer === "ok" ? route.fulfill({ json: { text: "and walk every state" } }) : route.fulfill({ status: 503, json: { error: "speech service unavailable" } });
  });
  const wave = v.main.locator(".composer-wave");
  const timer = v.main.getByLabel("Recording time", { exact: true });
  const transcribing = v.main.getByText("Transcribing…", { exact: true });

  await walk.open(project.path);
  await v.field.fill("Keep the draft");
  await walk.state("01-listening-three-controls-overlay", {
    action: () => v.mic.click(),
    visible: [v.stop, v.cancel, v.send, wave, timer, v.hint],
    hidden: [v.mic, transcribing, ...(v.phone ? [v.field] : [])],
  });
  await expect(v.hint).toHaveText("Listening… Stop to add text, or Send.");
  await expect(v.hint).toHaveAttribute("role", "status");
  await expect(v.field).toHaveValue("Keep the draft");
  await expect(v.field).toHaveAttribute("placeholder", "");
  await expect(timer).toHaveText(/^0:0\d$/);
  await expect(v.send).toBeEnabled();
  const row = await v.main.locator(".composer-row").boundingBox();
  await expect(v.send).toHaveText("");
  await expect(v.send.locator("svg")).toBeVisible();
  expect(row).not.toBeNull();
  for (const item of [v.cancel, wave, timer, v.stop, v.send]) {
    const box = await item.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThan(0);
    expect(box!.x).toBeGreaterThanOrEqual(row!.x);
    expect(box!.x + box!.width).toBeLessThanOrEqual(row!.x + row!.width + 1);
    expect(Math.abs(box!.y + box!.height / 2 - (row!.y + row!.height / 2))).toBeLessThan(1);
  }
  for (const control of [v.cancel, v.stop, v.send]) {
    await expect(control).toHaveCSS("width", v.phone ? "44px" : "36px");
    await expect(control).toHaveCSS("height", v.phone ? "44px" : "36px");
  }
  await walk.state("02-cancelled-with-esc-overlay", {
    action: () => page.keyboard.press("Escape"),
    visible: [v.mic, v.field],
    hidden: [v.stop, v.cancel, wave, timer, transcribing, ...(v.phone ? [v.hint] : [])],
  });
  await expect(v.field).toHaveValue("Keep the draft");
  expect(uploads).toEqual([]);

  await v.mic.click();
  await expect(v.stop).toBeVisible();
  await page.waitForTimeout(700);
  await walk.state("03-transcribing-overlay", {
    action: () => v.stop.click(),
    visible: [transcribing, v.field, ...(!v.phone ? [wave] : [])],
    hidden: [v.stop, v.cancel, ...(v.phone ? [wave] : [])],
  });
  await expect(v.mic).toBeDisabled();
  await expect(v.send).toBeDisabled();
  await expect(v.field).toBeEnabled();
  await v.field.fill("Keep the edited draft");
  release();
  await walk.state("04-landed-overlay", {
    action: () => expect(v.field).toHaveValue("Keep the edited draft and walk every state"),
    visible: [v.mic, v.send],
    hidden: [transcribing, wave, timer, v.main.getByText("and walk every state", { exact: true }), v.main.getByRole("region", { name: /transcript/i }), ...(v.phone ? [v.hint] : [])],
  });
  await expect(v.send).toBeEnabled();
  await expect(v.mic).toBeEnabled();
  expect(await v.main.locator(".composer button").allInnerTexts()).not.toContain("Undo");
  await expect(v.hint).toHaveText("L3 answers or creates one task. Shift + Enter for a new line.");
  expect(uploads[0]).toMatch(/^audio\//);

  answer = "fail";
  gate = Promise.resolve();
  await v.mic.click();
  await expect(v.stop).toBeVisible();
  await page.waitForTimeout(500);
  const failure = v.main.getByRole("alert").filter({ hasText: "Could not transcribe. Typing works." });
  await walk.state("05-transcription-failed-overlay", {
    action: () => v.send.click(),
    visible: [failure, v.mic],
    hidden: [transcribing, v.stop],
  });
  await expect(v.field).toHaveValue("Keep the edited draft and walk every state");
  await expect(v.mic).toBeEnabled();
});

test("voice: Send at once transcribes the draft into the normal pending bubble", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  await page.addInitScript(FAKE_MIC);
  await overlayChat(page, project.name, (live) => ({ ...live, active: null, busy: false, queued: [] }));
  let releaseTranscript: () => void = () => {};
  let releaseSend: () => void = () => {};
  const transcriptGate = new Promise<void>((resolve) => (releaseTranscript = resolve));
  const sendGate = new Promise<void>((resolve) => (releaseSend = resolve));
  const posts: string[] = [];
  await page.route((url) => url.pathname === "/api/transcribe", async (route) => {
    await transcriptGate;
    return route.fulfill({ json: { text: "and send this now" } });
  });
  await page.route((url) => url.pathname === "/api/chat", async (route) => {
    posts.push((route.request().postDataJSON() as { text: string }).text);
    await sendGate;
    return route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({ t: "Received the voice message." })}\n${JSON.stringify({ done: { turn_id: "ui-voice" } })}\n`,
    });
  });
  await walk.open(project.path);
  await v.field.fill("Keep the draft");
  await v.mic.click();
  await expect(v.stop).toBeVisible();
  await page.waitForTimeout(700);
  const transcribing = v.main.getByText("Transcribing…", { exact: true });
  await walk.state("01-send-at-once-transcribing-overlay", {
    action: () => v.send.click(),
    visible: [transcribing, v.field, ...(!v.phone ? [v.main.locator(".composer-wave")] : [])],
    hidden: [v.stop, v.cancel, ...(v.phone ? [v.main.locator(".composer-wave")] : [])],
  });
  await expect(v.send).toBeDisabled();
  await expect(v.mic).toBeDisabled();
  await expect(v.field).toHaveValue("Keep the draft");
  expect(posts).toEqual([]);
  const pending = v.convo.locator(".msg-row[data-pending]");
  releaseTranscript();
  await walk.state("02-send-at-once-bubble-at-60-overlay", {
    visible: [pending, v.bubble("Keep the draft and send this now")],
    hidden: [transcribing, v.main.locator(".composer-wave")],
  });
  await expect(pending).toHaveCSS("opacity", "0.6");
  await expect(v.field).toHaveValue("");
  expect(posts).toEqual(["Keep the draft and send this now"]);
  releaseSend();
  await walk.state("03-send-at-once-accepted-overlay", {
    visible: [v.convo.getByText("Received the voice message.", { exact: true })],
    hidden: [pending, transcribing],
  });
});

test("voice: denied and unavailable", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  await overlayChat(page, project.name, (live) => ({ ...live, active: null, busy: false }));
  await page.addInitScript(`
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
      configurable: true,
      value: async () => { throw new DOMException("Permission denied", "NotAllowedError"); },
    });
  `);
  await walk.open(project.path);
  await v.field.fill("Typing still works");
  await walk.state("01-denied-overlay", {
    action: () => v.mic.click(),
    visible: [v.main.getByText("Microphone blocked in the browser. Typing works.", { exact: true }), v.mic],
    hidden: [v.stop],
  });
  await expect(v.mic).toBeDisabled();
  await expect(v.field).toHaveValue("Typing still works");
  await expect(v.send).toBeEnabled();

  const insecure = await page.context().newPage();
  await overlayChat(insecure, project.name, (live) => ({ ...live, active: null, busy: false }));
  await insecure.addInitScript(`Object.defineProperty(window, "isSecureContext", { value: false });`);
  const w = views(insecure, info);
  const insecureWalk = walkthrough(insecure, info);
  await insecureWalk.open(project.path);
  await insecureWalk.state("02-unavailable-voice-needs-https-overlay", {
    visible: [w.main.getByText("Voice needs HTTPS", { exact: true }), w.field, w.send],
    hidden: [w.mic, w.stop],
  });
  await insecure.close();
});

test("multiline draft grows within its cap, scrolls internally, and preserves bottom or older reading", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const history: Row[] = Array.from({ length: 40 }, (_, index) => ({
    at: now(), role: "assistant", trigger: "chat", turn_id: `draft-history-${index}`,
    text: `Message ${index + 1}. Keep this earlier discussion readable while a draft grows.`,
  }));
  await overlayChat(page, project.name, (live) => ({ ...live, history, active: null, busy: false, queued: [] }));
  await walk.open(project.path);
  await page.evaluate(async () => { await document.fonts.ready; });
  const scroll = v.main.locator(".convo-scroll");
  const bottomGap = () => scroll.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight);
  const longDraft = Array.from({ length: 24 }, (_, index) => `Draft line ${index + 1}`).join("\n");
  await walk.state("01-one-line-empty", { visible: [v.field, v.send], hidden: [] });
  await expect(v.field).toHaveCSS("height", v.phone ? "44px" : "24px");
  if (v.phone) {
    await expect(v.main.locator(".composer-box")).toHaveCSS("height", "54px");
    await expect(v.hint).toBeHidden();
  }
  await scroll.evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await walk.state("02-long-draft-at-bottom", {
    action: () => v.field.fill(longDraft), visible: [v.field, v.send], hidden: [],
  });
  await expect(v.field).toHaveCSS("height", v.phone ? "120px" : "360px");
  await expect.poll(bottomGap).toBeLessThanOrEqual(1);
  expect(await v.field.evaluate((node) => node.scrollHeight)).toBeGreaterThan(await v.field.evaluate((node) => node.clientHeight));
  await v.field.evaluate((node) => { node.scrollTop = node.scrollHeight; });
  expect(await v.field.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  await v.field.fill("");
  await expect(v.field).toHaveCSS("height", v.phone ? "44px" : "24px");
  await expect.poll(bottomGap).toBeLessThanOrEqual(1);

  await scroll.evaluate(async (node) => {
    // Finish the draft resize before the separate action of reading older messages.
    const painted = () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    await painted();
    node.scrollTop = 0;
    await painted();
  });
  await expect.poll(() => scroll.evaluate((node) => node.scrollTop)).toBe(0);
  const firstMessage = v.convo.getByText(history[0]!.text, { exact: true });
  await expect(firstMessage).toBeInViewport();
  const firstTop = (await firstMessage.boundingBox())!.y;
  await walk.state("03-long-draft-while-reading-older", {
    action: () => v.field.fill(longDraft), visible: [firstMessage, v.field], hidden: [],
  });
  await expect(v.field).toHaveCSS("height", v.phone ? "120px" : "360px");
  await expect.poll(async () => (await firstMessage.boundingBox())!.y).toBe(firstTop);
  await expect.poll(() => scroll.evaluate((node) => node.scrollTop)).toBe(0);
  await walk.state("04-draft-collapses-reading-stays", {
    action: () => v.field.fill("Short draft"), visible: [firstMessage, v.field], hidden: [],
  });
  await expect(v.field).toHaveCSS("height", v.phone ? "44px" : "24px");
  await expect.poll(async () => (await firstMessage.boundingBox())!.y).toBe(firstTop);
});

test("the phone shows the project name once, in the header, with the composer above the tab bar @phone-only", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  await walk.open(project.path);
  const title = page.getByRole("heading", { level: 1, name: project.name, exact: true });
  await walk.state("01-name-once", {
    visible: [title, v.field],
    hidden: [v.main.locator(".project-header")],
  });
  await expect(title).toHaveCount(1);
  const field = await v.field.boundingBox();
  const bar = await page.getByRole("navigation", { name: "Primary", exact: true }).boundingBox();
  expect(field && bar && field.y + field.height <= bar.y).toBe(true);
});

test("phone-shell-is-fixed-only-inner-containe: viewport shrink keeps the newest conversation row above the composer", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const task = await fixtureTask(request, project.name);
  const walk = walkthrough(page, info);
  await page.route((url) => url.pathname === `/api/task/${project.name}/${task.slug}`,
    (route) => route.fulfill({ json: { ...task, state: "running" } }));
  // A keyboard changes only visualViewport on iOS; browser emulation has no native keyboard.
  await page.addInitScript(() => {
    const viewport = new EventTarget();
    let height: number | undefined;
    Object.defineProperty(viewport, "height", { get: () => height ?? window.innerHeight, set: (value: number) => { height = value; } });
    Object.assign(viewport, { offsetTop: 0, scale: 1 });
    Object.defineProperty(window, "visualViewport", { value: viewport });
  });
  for (const [label, path] of [["project", project.path], ["task", `${project.path}/tasks/${task.slug}`]]) {
    await walk.open(path);
    const scroll = page.locator(".convo-scroll");
    const field = page.getByRole("main").getByRole("textbox").first();
    await expect(scroll.locator(".convo-col")).toBeVisible();
    await page.evaluate(async () => { await document.fonts.ready; });
    await expect(page.locator(".shell")).toHaveCSS("height", `${page.viewportSize()!.height}px`);
    const height = await scroll.evaluate((node) => node.clientHeight);
    await expect(field).toBeVisible();
    await field.focus();
    await scroll.evaluate((node) => { node.scrollTop = node.scrollHeight; });
    await page.evaluate(() => {
      Object.assign(window.visualViewport!, { height: 480, offsetTop: 24 });
      window.visualViewport!.dispatchEvent(new Event("resize"));
    });
    if (info.project.name === "phone") {
      await expect(page.locator(".shell")).toHaveCSS("height", "480px");
      expect(await scroll.evaluate((node) => node.clientHeight)).toBeLessThan(height);
      await expect.poll(() => scroll.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThanOrEqual(1);
      const column = await scroll.locator(".convo-col").boundingBox();
      const box = (await scroll.boundingBox())!;
      expect(column!.y + column!.height).toBeLessThanOrEqual(box.y + box.height);
    } else {
      expect(await scroll.evaluate((node) => node.clientHeight)).toBe(height);
    }
    await walk.state(`${label}-keyboard-viewport-overlay`, { visible: [scroll], hidden: [] });
    await page.evaluate(() => {
      Object.assign(window.visualViewport!, { height: window.innerHeight, offsetTop: 0 });
      window.visualViewport!.dispatchEvent(new Event("resize"));
    });
    await expect.poll(() => scroll.evaluate((node) => node.clientHeight)).toBe(height);
    await walk.state(`${label}-keyboard-dismissed-overlay`, { visible: [scroll], hidden: [] });
  }
});
