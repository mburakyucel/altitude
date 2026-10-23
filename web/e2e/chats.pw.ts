import { expect, type Page, type Route } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Agent chats (SPEC.md §3.18, docs/ARCHITECTURE.md#agent-chats), walked at both widths: Work's Agent chats section
 * with none, with chats in every state and archived ones folded; a new chat from its empty page to its first turn,
 * with an image; a chat answering with its steps folded, a message that waits and Remove, Stop; a reply that could not
 * come, one cut off and Retry, a new session and a restart; the menu, Rename, Archive with and without changes, the
 * archived chat, Reopen and Delete; Make a task with and without changes, refused, making and made with the task's
 * card, and Make a task first from Archive; a failed and a missing read; Monitor's row. The service is real; the chats are
 * fictional fixtures behind routes, so no agent runs.
 */

type Json = Record<string, unknown>;
const cid = (digit: string) => digit.repeat(12);
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

// An overview or monitor read still in flight at teardown would find its response disposed.
test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "wait" }); });

const ENGINE = { engine: "claude", model: "claude-opus-5-5" };
const ASK1 = "Why does the CSV export take 40 seconds on the big workspace? Find out, don’t change anything yet.";
const REPLY1 = "The export loads every row’s comments one query at a time: 18,000 queries for the big workspace. Loading them in one query should bring it under two seconds. Want me to try it here and time it?";
const ASK2 = "Yes, try it and run the export tests.";
const REPLY2 = "Done. One query for all comments: the big workspace exports in 1.6 seconds, and the 31 export tests pass.";

const step = (verb: string, text: string, output = "", at = ago(58)) => ({ kind: "step", at, verb, text, detail: text, output, exit: verb === "failed" ? 1 : null });
const STARTED = { kind: "event", event: "started", at: ago(60), ...ENGINE };
const YOU1 = { kind: "you", id: "m1", at: ago(60), text: ASK1, images: [], waiting: false };
const TURN1 = { kind: "turn", n: 1, state: "done", started: ago(60), ended: ago(57), ...ENGINE, engine_model: ENGINE.model, answer: REPLY1, error: null, stop: null,
  steps: [{ kind: "say", at: ago(59), text: "I’ll profile the export first." }, step("ran", "python -m cProfile -s cumtime scripts/export.py big", "18000 calls to comments_for"),
    step("ran", "grep -n comments_for export/rows.py", "41:    for comment in comments_for(row):")] };
const YOU2 = { kind: "you", id: "m2", at: ago(3), text: ASK2, images: [], waiting: false };
const LIVE = { kind: "turn", n: 2, state: "answering", started: ago(3), ended: null, ...ENGINE, engine_model: ENGINE.model, answer: null, error: null, stop: null,
  steps: [step("changed", "export/rows.py", "", ago(2)), step("running", "pytest tests/test_export.py -q", "", ago(1))] };
const IMAGE = { id: "img1", name: "export.png", mime_type: "image/png", size: 2048, width: 320, height: 180, source_message_id: "m1" };
const DONE2 = { ...LIVE, state: "done", ended: ago(0), answer: REPLY2, steps: [step("changed", "export/rows.py", "", ago(2)), step("ran", "pytest tests/test_export.py -q", "31 passed", ago(1))] };

function chat(extra: Json = {}): Json {
  return { id: cid("a"), name: "Why is the export slow?", state: "idle", ...ENGINE, archived: null, created: ago(60),
    latest: { at: ago(57), by: "agent" }, choice: null, effort: null, items: [STARTED, YOU1, TURN1], changes: 0, context: 12, ...extra };
}

const row = (id: string, name: string, state: string, latest: Json, extra: Json = {}) =>
  ({ id: cid(id), name, state, ...ENGINE, archived: null, created: ago(600), latest, ...extra });
const ROWS = [
  row("b", "New chat", "starting", { at: ago(0), by: "you" }),
  row("a", "Why is the export slow?", "answering", { at: ago(3), by: "you" }),
  row("c", "Try the new index on a copy of the fixture data", "idle", { at: ago(12), by: "agent" }),
  row("d", "Sketch the beta stage’s config", "failed", { at: ago(60), by: "you" }),
  row("e", "Flaky pagination test, the one that fails only on the first run after midnight in the fixture calendar", "idle", { at: ago(26 * 60), by: "you" }),
  row("f", "Bisect the slow build", "archived", { at: ago(3 * 24 * 60), by: "agent" }, { archived: ago(2 * 24 * 60) }),
];

/** The project's chats behind every read and action route; `post` decides what each POST returns. */
type Reply = { status: number; json: Json };
async function chats(page: Page, state: { listing: Json[]; detail: Record<string, Json>; post?: (path: string, body: Json) => Reply | Promise<Reply> }) {
  const posts: { path: string; body: Json }[] = [];
  let fail = false;
  let failed = 0;
  await page.route(/\/api\/chats\/atlas(\/[0-9a-f]{12}(\/[a-z]+)?)?$/, async (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as Json;
      posts.push({ path, body });
      const reply = await state.post!(path, body);
      return route.fulfill({ status: reply.status, json: reply.json });
    }
    if (fail) {
      failed += 1;
      return route.fulfill({ status: 500, json: { error: "fixture" } });
    }
    const id = path.split("/")[4];
    if (!id) return route.fulfill({ json: { chats: state.listing } });
    const found = state.detail[id];
    return found ? route.fulfill({ json: found }) : route.fulfill({ status: 404, json: { error: "There is no such chat." } });
  });
  return { posts, failing: (value: boolean) => { fail = value; }, failed: () => failed };
}

const reply = (json: Json) => ({ status: 200, json });

test("Work's Agent chats: none, every state, archived folded, and a row opens its chat", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const work = page.getByRole("region", { name: "Work", exact: true });
  const section = work.getByRole("region", { name: "Agent chats" });
  const link = (name: RegExp) => section.getByRole("link", { name });
  const state = { listing: [] as Json[], detail: { [cid("a")]: chat({ state: "answering", items: [STARTED, YOU1, TURN1, YOU2, LIVE] }) } };
  const api = await chats(page, state);
  const openWork = async () => {
    await walk.open(phone ? "/projects/atlas?tab=work" : "/projects/atlas");
    await expect(work.getByText(/current · \d+ done this week/)).toBeVisible();
  };

  await openWork();
  await walk.state("01-no-chats", {
    action: () => section.scrollIntoViewIfNeeded(),
    visible: [section.getByRole("heading", { name: "Agent chats", exact: true }), section.getByRole("link", { name: "New chat" })],
    hidden: [section.getByText("Archived"), section.locator(".task-card")],
  });
  // Agent chats sit after Done this week, before Routines and Altitude repairs.
  const order = await work.locator(".wp-section, .wp-fold, .wp-agents").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label") ?? node.className));
  expect(order.indexOf("Agent chats")).toBeGreaterThan(order.indexOf("Current"));

  state.listing = ROWS;
  await openWork();
  await walk.state("02-chats", {
    action: () => section.scrollIntoViewIfNeeded(),
    visible: [section.getByRole("heading", { name: "Agent chats (5)" }), link(/^New chat · Starting · Opus 5 on /), link(/^Why is the export slow\? · Answering · Opus 5 on /),
      link(/^Try the new index .* · Replied 12 min ago$/), link(/^Sketch the beta stage’s config · Couldn’t answer · 1h ago$/),
      link(/^Flaky pagination test.* · You wrote yesterday$/), section.getByText("Archived · 1")],
    hidden: [link(/^Bisect the slow build/)],
  });
  expect(await section.locator(".wp-row:not([data-archived]) .task-card-dot").evaluateAll((dots) => dots.map((dot) => dot.getAttribute("data-state")))).toEqual(["running", "running", "idle", "danger", "idle"]);
  // A long name stays on one line.
  const long = section.locator(".chat-row-name").filter({ hasText: "Flaky pagination test" });
  expect(await long.evaluate((node) => node.scrollWidth > node.clientWidth && getComputedStyle(node).whiteSpace === "nowrap")).toBe(true);

  await section.getByText("Archived · 1").click();
  await walk.state("03-archived-open", { visible: [link(/^Bisect the slow build · Archived /)], hidden: [] });

  // A failed refresh keeps the rows it has.
  // The list is read again every few seconds while a chat answers.
  api.failing(true);
  const seen = api.failed();
  await expect.poll(() => api.failed(), { timeout: 15_000 }).toBeGreaterThan(seen);
  await walk.state("04-refresh-failed", { visible: [link(/^Why is the export slow\?/)], hidden: [work.getByText(/Could not load .*chats/)] });
  api.failing(false);

  // A row opens the chat; Work stays lit on the phone and the crumb leads back.
  await link(/^Why is the export slow\?/).click();
  await expect(page).toHaveURL(new RegExp(`/projects/atlas/chats/${cid("a")}$`));
  await expect(page.getByRole("heading", { level: 1, name: "Why is the export slow?" })).toBeVisible();
  const primary = page.getByRole("navigation", { name: "Primary" });
  if (phone) {
    await walk.state("05-opened-from-work", { visible: [primary.getByRole("link", { name: "Work", exact: true })], hidden: [] });
    await expect(primary.getByRole("link", { name: "Work", exact: true })).toHaveAttribute("aria-current", "page");
    await page.getByRole("button", { name: "Back", exact: true }).click();
  } else {
    await walk.state("05-opened-from-work", { visible: [page.getByRole("button", { name: "‹ Work" })], hidden: [] });
    await page.getByRole("button", { name: "‹ Work" }).click();
  }
  await expect(page).toHaveURL(/\/projects\/atlas/);
  await expect(section).toBeVisible();
});

test("a new chat: its empty page, the first message with an image, and its first turn starting", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const state = { listing: [] as Json[], detail: {} as Record<string, Json>, post: (_path: string, _body: Json) => reply({}) };
  const api = await chats(page, state);
  await page.route(/\/api\/images\/atlas(\?.*)?$/, (route) => route.fulfill({ json: { available: true, reason: null, max_count: 4,
    max_bytes: 10 << 20, max_total_bytes: 20 << 20, max_pixels: 25_000_000, max_dimension: 8192 } }));
  const png = await page.evaluate(() => {
    const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 180;
    const context = canvas.getContext("2d")!;
    context.fillStyle = "#f8fafc"; context.fillRect(0, 0, 320, 180);
    context.fillStyle = "#172554"; context.font = "bold 22px sans-serif"; context.fillText("Export: 40 s", 20, 50);
    return canvas.toDataURL("image/png").split(",")[1]!;
  });
  await page.route(/\/api\/images\/atlas\/img1$/, (route) => route.fulfill({ contentType: "image/png", body: Buffer.from(png, "base64") }));

  await walk.open(phone ? "/projects/atlas?tab=work" : "/projects/atlas");
  await page.getByRole("region", { name: "Agent chats" }).getByRole("link", { name: "New chat" }).click();
  await expect(page).toHaveURL(/\/projects\/atlas\/chats\/new$/);
  const composer = page.getByRole("textbox", { name: "Message the agent" });
  const convo = page.getByRole("region", { name: "Chat conversation" });
  await walk.state("01-new-chat", {
    visible: [page.getByRole("heading", { level: 1, name: "New chat" }), page.getByText("Works in its own copy of atlas’s code, made from main when you send."),
      composer, page.getByRole("button", { name: /^Model: Default · Default effort$/ }), page.getByRole("button", { name: "Add images" })],
    hidden: [page.getByRole("button", { name: "Chat actions" }), page.getByRole("button", { name: /^Stop/ }), page.locator(".task-chips")],
  });
  if (!phone) await expect(composer).toBeFocused();

  // The first message makes the chat: it opens at its own address, its copy is being made and the turn starts.
  const made = chat({ state: "starting", latest: { at: ago(0), by: "you" }, changes: null, items: [
    { ...YOU1, at: ago(0), images: [IMAGE] },
    { ...LIVE, n: 1, state: "starting", started: ago(0), steps: [] }] });
  state.post = (path, body) => {
    expect(path).toBe("/api/chats/atlas");
    expect(body.text).toBe(ASK1);
    expect(body.choice).toBeNull();
    expect((body.images as Json[])[0]!.name).toBe("export.png");
    state.detail[cid("a")] = made;
    return reply(made);
  };
  await page.getByLabel("Choose images").setInputFiles({ name: "export.png", mimeType: "image/png", buffer: Buffer.from(png, "base64") });
  await composer.fill(ASK1);
  await composer.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/projects/atlas/chats/${cid("a")}$`));
  await walk.state("02-starting", {
    visible: [page.getByRole("heading", { level: 1, name: "Why is the export slow?" }), page.getByText(ASK1), page.getByRole("img", { name: "export.png" }),
      convo.getByText("Making its copy of main…"), convo.getByText(/^Starting · Opus 5 on /), page.getByRole("button", { name: /^Stop/ }),
      ...(phone ? [page.locator(".phone-status").getByText(/^Starting · Opus 5 on /)] : [page.locator(".task-chips").getByText("Starting")])],
    hidden: [page.getByText("Works in its own copy"), page.getByRole("button", { name: "Retry" })],
  });
  await expect(composer).toHaveValue("");
  expect(api.posts).toHaveLength(1);

  // The turn opens: the started line, the steps as they come, and the reply.
  state.detail[cid("a")] = chat({ items: [STARTED, { ...YOU1, images: [IMAGE] }, TURN1] });
  await page.reload();
  await walk.state("03-replied", {
    visible: [page.getByText(/^Started · Opus 5 on .* · its own copy of main$/), page.getByText("Worked 3 min · 2 steps"), page.getByText(/^The export loads every row’s comments/),
      page.getByRole("button", { name: "Chat actions" })],
    hidden: [page.getByText("Making its copy of main…"), page.getByRole("button", { name: /^Stop/ }), page.getByText("I’ll profile the export first.")],
  });
  await page.getByText("Worked 3 min · 2 steps").click();
  await walk.state("04-steps-open", {
    visible: [page.getByText("I’ll profile the export first."), page.locator(".agent-step").filter({ hasText: "cProfile" }).getByText("1 line")],
    hidden: [],
  });
});

test("a chat answering: folded steps, a message that waits, Remove, and Stop", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const answering = chat({ state: "answering", latest: { at: ago(3), by: "you" }, changes: 1, items: [STARTED, YOU1, TURN1, YOU2, LIVE] });
  const state = { listing: [] as Json[], detail: { [cid("a")]: answering } as Record<string, Json>, post: (_path: string, _body: Json) => reply(answering) };
  const api = await chats(page, state);
  const composer = page.getByRole("textbox", { name: "Message the agent" });
  const stop = page.getByRole("button", { name: /^Stop/ });

  await walk.open(`/projects/atlas/chats/${cid("a")}`);
  await walk.state("01-answering", {
    visible: [page.getByText(/^Working · 3 min · 2 steps$/), page.locator(".chat-latest").getByText("pytest tests/test_export.py -q"), stop, composer,
      ...(phone ? [page.locator(".phone-status").getByText(/^Answering · Opus 5 on /)]
        : [page.locator(".task-chips").getByText("Answering"), page.locator(".task-chips").getByText(/^Opus 5 on /), page.getByRole("button", { name: "Chat actions" })])],
    hidden: [page.locator(".chat-latest").getByText("export/rows.py"), ...(phone ? [page.getByRole("button", { name: "Chat actions" })] : [])],
  });
  if (!phone) await expect(stop.locator("kbd")).toHaveText("Esc");

  // A message while it answers waits for the reply and goes with the next turn; Remove takes it back.
  const waiting = { kind: "you", id: "m3", at: ago(0), text: "Also check the PDF export", images: [], waiting: true };
  state.post = (path, body) => {
    expect(path).toBe(`/api/chats/atlas/${cid("a")}/message`);
    expect(body.text).toBe("Also check the PDF export");
    state.detail[cid("a")] = { ...answering, items: [...answering.items as Json[], waiting] };
    return reply(state.detail[cid("a")]!);
  };
  await composer.fill("Also check the PDF export");
  await composer.press("Enter");
  const remove = page.getByRole("button", { name: "Remove", exact: true });
  await walk.state("02-message-waits", { visible: [page.getByText("Also check the PDF export"), page.getByText("Waits for the reply ·"), remove], hidden: [] });
  state.post = (path, body) => {
    expect(path).toBe(`/api/chats/atlas/${cid("a")}/remove`);
    expect(body.message).toBe("m3");
    state.detail[cid("a")] = answering;
    return reply(answering);
  };
  await remove.click();
  await walk.state("03-removed", { visible: [stop], hidden: [page.getByText("Also check the PDF export"), page.getByText("Waits for the reply")] });

  // Stop ends the turn at once: Escape on desktop, the header button on the phone.
  const stopping = { ...answering, items: [STARTED, YOU1, TURN1, YOU2, { ...LIVE, stop: true }] };
  state.post = (path) => {
    expect(path).toBe(`/api/chats/atlas/${cid("a")}/stop`);
    state.detail[cid("a")] = stopping;
    return reply(stopping);
  };
  if (phone) await stop.click();
  else {
    await page.locator("body").click({ position: { x: 700, y: 300 } });
    await page.keyboard.press("Escape");
  }
  await walk.state("04-stopping", { visible: [page.getByRole("button", { name: /Stopping/ })], hidden: [] });
  await expect(page.getByRole("button", { name: /Stopping/ })).toBeDisabled();
  expect(api.posts.filter((post) => post.path.endsWith("/stop"))).toHaveLength(1);
  state.detail[cid("a")] = { ...answering, state: "idle", items: [STARTED, YOU1, TURN1, YOU2,
    { ...LIVE, state: "stopped", ended: ago(0), stop: true, steps: [LIVE.steps[0], { ...LIVE.steps[1], verb: "cut" }] }] };
  await page.reload();
  await walk.state("05-stopped", {
    visible: [page.getByText("Stopped by you · the command it was running was cut off"), composer],
    hidden: [stop, page.getByText(/^Working ·/)],
  });
  await expect(page.getByText(/^Worked 3 min · 2 steps$/), "both turns fold to their line").toHaveCount(2);
});

test("a reply that could not come: failed, cut off, Retry, a new session and a restart", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const failed = chat({ state: "failed", items: [STARTED, YOU1, TURN1, YOU2,
    { ...LIVE, state: "failed", ended: ago(2), steps: [], error: "The weekly allowance is used." }] });
  const state = { listing: [] as Json[], detail: { [cid("a")]: failed } as Record<string, Json>, post: (_path: string, _body: Json) => reply(failed) };
  const api = await chats(page, state);
  await walk.open(`/projects/atlas/chats/${cid("a")}`);
  const retry = page.getByRole("button", { name: "Retry", exact: true });
  await walk.state("01-failed", {
    visible: [page.getByRole("alert").getByText("Couldn’t answer: The weekly allowance is used. Choose another model below, or Retry after it resets."), retry,
      page.getByRole("button", { name: /^Model: / })],
    hidden: [page.getByRole("button", { name: /^Stop/ })],
  });

  const cut = chat({ state: "failed", items: [STARTED, YOU1, TURN1, YOU2, { ...LIVE, state: "cut", ended: ago(1), steps: [LIVE.steps[0], { ...LIVE.steps[1], verb: "cut" }] }] });
  state.detail[cid("a")] = cut;
  await page.reload();
  const convo = page.getByRole("region", { name: "Chat conversation" });
  await walk.state("02-cut-off", { visible: [page.getByText("The reply was cut off: Altitude stopped while it ran."), retry], hidden: [convo.getByText(/Couldn’t answer/)] });

  // Retry sends the waiting words again; the turn starts on another engine's new session, and a restart did not stop it.
  const again = chat({ state: "answering", engine: "codex", model: "gpt-fixture", items: [STARTED, YOU1, TURN1, YOU2,
    { kind: "event", event: "new_session", at: ago(0), engine: "codex", model: "gpt-fixture" },
    { kind: "event", event: "restarted", at: ago(0) },
    { ...LIVE, started: ago(0), engine: "codex", model: "gpt-fixture", engine_model: "gpt-fixture", steps: [] }] });
  state.post = (path) => {
    expect(path).toBe(`/api/chats/atlas/${cid("a")}/retry`);
    state.detail[cid("a")] = again;
    return reply(again);
  };
  await retry.click();
  await walk.state("03-retried", {
    visible: [page.getByText(/^New session on .* · it was given this conversation so far$/), page.getByText("Altitude restarted · the reply continued"),
      page.getByText(/^Working · \d+ min · 0 steps$/)],
    hidden: [page.getByText("The reply was cut off"), retry],
  });
  expect(api.posts.filter((post) => post.path.endsWith("/retry"))).toHaveLength(1);
});

test("the menu, Rename, Archive with and without changes, Reopen and Delete", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const done = chat({ changes: 1, items: [STARTED, YOU1, TURN1, YOU2, DONE2] });
  const state = { listing: [] as Json[], detail: { [cid("a")]: done } as Record<string, Json>, post: (_path: string, _body: Json) => reply(done) };
  const api = await chats(page, state);
  await walk.open(`/projects/atlas/chats/${cid("a")}`);
  const more = page.getByRole("button", { name: "Chat actions" });
  const menu = page.getByRole("menu", { name: "Chat actions" });

  await more.click();
  await walk.state("01-menu", {
    visible: [menu.getByRole("menuitem", { name: "Rename" }), menu.getByRole("menuitem", { name: "Make a task…" }), menu.getByRole("menuitem", { name: "Archive" }),
      menu.getByText(/^Opus 5 on .* · started .* · its copy: 1 changed file$/)],
    hidden: [],
  });

  // Rename in place: Escape restores, Enter saves.
  await menu.getByRole("menuitem", { name: "Rename" }).click();
  const field = page.getByRole("textbox", { name: "Chat name" });
  await walk.state("02-rename", { visible: [field, page.getByRole("button", { name: "Save" }), page.getByRole("button", { name: "Cancel" })], hidden: [more] });
  await expect(field).toHaveValue("Why is the export slow?");
  await field.fill("Faster export");
  await field.press("Escape");
  await expect(page.getByRole("heading", { level: 1, name: "Why is the export slow?" })).toBeVisible();
  state.post = (path, body) => {
    expect(path).toBe(`/api/chats/atlas/${cid("a")}/rename`);
    state.detail[cid("a")] = { ...done, name: body.name };
    return reply(state.detail[cid("a")]!);
  };
  await more.click();
  await menu.getByRole("menuitem", { name: "Rename" }).click();
  await field.fill("Faster export");
  await field.press("Enter");
  await walk.state("03-renamed", { visible: [page.getByRole("heading", { level: 1, name: "Faster export" }), more], hidden: [field] });

  // Archive asks when its copy has changes no task has.
  await more.click();
  await menu.getByRole("menuitem", { name: "Archive" }).click();
  const sheet = page.getByRole("dialog", { name: "Archive this chat?" });
  await walk.state("04-archive-asks", {
    visible: [sheet.getByText(/Its copy of the code has changes no task has: 1 changed file\. Archiving deletes them\./),
      sheet.getByRole("button", { name: "Archive and delete changes" }), sheet.getByRole("button", { name: "Cancel" })],
    hidden: [],
  });
  const archived = { ...state.detail[cid("a")]!, state: "archived", archived: ago(0), changes: null,
    items: [...(done.items as Json[]), { kind: "event", event: "archived", at: ago(0) }] };
  state.post = (path, body) => {
    expect(path).toBe(`/api/chats/atlas/${cid("a")}/archive`);
    expect(body.discard).toBe(true);
    state.detail[cid("a")] = archived;
    return reply(archived);
  };
  await sheet.getByRole("button", { name: "Archive and delete changes" }).click();
  const composer = page.getByRole("textbox", { name: "Message the agent" });
  await walk.state("05-archived", {
    visible: [page.getByText("Archived by you"), page.getByText(/^Archived · /), page.getByRole("button", { name: "Reopen" }), page.getByRole("button", { name: "Delete…" })],
    hidden: [composer, sheet, more],
  });

  // Reopen makes a fresh copy and the chat goes on.
  const reopened = { ...done, name: "Faster export", changes: 0, items: [...(archived.items as Json[]), { kind: "event", event: "reopened", at: ago(0) }] };
  state.post = (path) => {
    expect(path).toBe(`/api/chats/atlas/${cid("a")}/reopen`);
    state.detail[cid("a")] = reopened;
    return reply(reopened);
  };
  await page.getByRole("button", { name: "Reopen" }).click();
  await walk.state("06-reopened", { visible: [page.getByText("Reopened · a fresh copy of main"), composer, more], hidden: [page.getByRole("button", { name: "Reopen" })] });

  // Without changes Archive acts at once.
  state.post = (path, body) => {
    expect(body.discard).toBe(false);
    state.detail[cid("a")] = { ...archived, name: "Faster export" };
    return reply(state.detail[cid("a")]!);
  };
  await more.click();
  await menu.getByRole("menuitem", { name: "Archive" }).click();
  await expect(page.getByRole("button", { name: "Delete…" })).toBeVisible();
  await expect(sheet).toBeHidden();

  // Delete asks, then removes the chat for good and returns to Work.
  await page.getByRole("button", { name: "Delete…" }).click();
  const dialog = page.getByRole("dialog", { name: "Delete this chat?" });
  await walk.state("07-delete-asks", {
    visible: [dialog.getByText("Its conversation and session are removed for good."), dialog.getByRole("button", { name: "Delete chat" }), dialog.getByRole("button", { name: "Cancel" })],
    hidden: [],
  });
  state.post = (path) => {
    expect(path).toBe(`/api/chats/atlas/${cid("a")}/delete`);
    delete state.detail[cid("a")];
    return reply({ deleted: cid("a") });
  };
  await dialog.getByRole("button", { name: "Delete chat" }).click();
  await expect(page).toHaveURL(/\/projects\/atlas\?tab=work$/);
  await walk.state("08-deleted", { visible: [page.getByRole("region", { name: "Agent chats" })], hidden: [dialog] });
  expect(api.posts.map((post) => post.path.split("/").pop())).toEqual(["rename", "archive", "reopen", "archive", "delete"]);
  if (phone) await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Work", exact: true })).toHaveAttribute("aria-current", "page");
});

test("Make a task: the sheet with and without changes, refused, making, the task's card, and Make a task first", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const done = chat({ changes: 1, items: [STARTED, YOU1, TURN1, YOU2, DONE2] });
  const state = { listing: [] as Json[], detail: { [cid("a")]: done } as Record<string, Json>,
    post: (_path: string, _body: Json): Reply | Promise<Reply> => reply(done) };
  const api = await chats(page, state);
  // The task the press makes, as the project's task list reads it once made.
  let listed = false;
  await page.route((url) => url.pathname === "/api/project/atlas", async (route) => {
    const response = await route.fetch();
    const json = await response.json() as { tasks: Json[] };
    if (listed) json.tasks.push({ slug: "faster-export", title: "Load export comments in one query", state: "queued", updated: ago(0) });
    await route.fulfill({ response, json });
  });
  await walk.open(`/projects/atlas/chats/${cid("a")}`);
  const more = page.getByRole("button", { name: "Chat actions" });
  const menu = page.getByRole("menu", { name: "Chat actions" });
  const sheet = page.getByRole("dialog", { name: "Make a task from this chat" });
  const title = sheet.getByRole("textbox", { name: "Title" });
  const deliver = sheet.getByRole("textbox", { name: "What should it deliver?" });
  const make = sheet.getByRole("button", { name: "Make task" });
  const cancel = sheet.getByRole("button", { name: "Cancel" });
  const openSheet = async () => { await more.click(); await menu.getByRole("menuitem", { name: "Make a task…" }).click(); };

  await openSheet();
  await walk.state("01-task-sheet", {
    visible: [title, deliver, sheet.getByText("Its changes (1 file) move to the task, with this conversation as its context. The chat goes on in a fresh copy of main."), make, cancel],
    hidden: [sheet.getByText("It starts from main")],
  });
  await expect(title).toHaveValue("Why is the export slow?");
  await expect(deliver).toHaveAttribute("placeholder", "For example: ship the faster export with a test");
  await expect(make).toBeDisabled();
  await cancel.click();
  await expect(sheet).toBeHidden();
  expect(api.posts).toHaveLength(0);

  // Both fields are needed; a refusal says why and keeps what was written.
  await openSheet();
  await deliver.fill("Make the CSV and PDF exports load comments in one query, with a test for the big workspace.");
  await title.fill(" ");
  await expect(make).toBeDisabled();
  await title.fill("Load export comments in one query");
  state.post = () => ({ status: 409, json: { error: "Make a task when the reply has ended." } });
  await make.click();
  await walk.state("02-refused", { visible: [sheet.getByRole("alert").getByText("Make a task when the reply has ended."), make], hidden: [] });
  await expect(deliver).toHaveValue(/^Make the CSV and PDF exports/);

  // Making…, then the line and the task's card under it; the chat goes on.
  const made = { ...done, changes: null, items: [...(done.items as Json[]),
    { kind: "event", event: "task", at: ago(0), slug: "faster-export", moved: 1 }] };
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => { release = resolve; });
  state.post = async (path, body) => {
    expect(path).toBe(`/api/chats/atlas/${cid("a")}/task`);
    expect(body).toMatchObject({ title: "Load export comments in one query", text: "Make the CSV and PDF exports load comments in one query, with a test for the big workspace." });
    await held;
    state.detail[cid("a")] = made;
    listed = true;
    return reply(made);
  };
  await make.click();
  await walk.state("03-making", { visible: [sheet.getByRole("button", { name: "Making…" })], hidden: [] });
  await expect(cancel).toBeDisabled();
  release();
  const card = page.getByRole("link", { name: /^Load export comments in one query/ });
  await walk.state("04-task-made", {
    visible: [page.getByText("Task made by you · its changes moved to the task; a fresh copy of main"), card, page.getByRole("textbox", { name: "Message the agent" })],
    hidden: [sheet],
  });
  await expect(card).toHaveAttribute("href", "/projects/atlas/tasks/faster-export");
  // One request identity for the sheet: the refused press and the one that made the task are the same request.
  const tasks = api.posts.filter((post) => post.path.endsWith("/task"));
  expect(tasks).toHaveLength(2);
  expect(tasks[0]!.body.request).toBe(tasks[1]!.body.request);

  // A chat without changes starts the task from main.
  await openSheet();
  await walk.state("05-sheet-without-changes", { visible: [sheet.getByText("It starts from main, with this conversation as its context.")], hidden: [sheet.getByText(/^Its changes/)] });
  await cancel.click();

  // Archive with changes offers Make a task first, which opens the sheet.
  state.detail[cid("a")] = done;
  await page.reload();
  await more.click();
  await menu.getByRole("menuitem", { name: "Archive" }).click();
  const archive = page.getByRole("dialog", { name: "Archive this chat?" });
  await walk.state("06-archive-offers-a-task", {
    visible: [archive.getByRole("button", { name: "Make a task first" }), archive.getByRole("button", { name: "Archive and delete changes" })], hidden: [],
  });
  await archive.getByRole("button", { name: "Make a task first" }).click();
  await walk.state("07-make-a-task-first", { visible: [title, deliver], hidden: [archive] });
  await cancel.click();

  // While it answers, Make a task waits for the reply (the phone shows Stop in ⋯'s place).
  if (!phone) {
    state.detail[cid("a")] = chat({ state: "answering", changes: 1, items: [STARTED, YOU1, TURN1, YOU2, LIVE] });
    await page.reload();
    await more.click();
    const item = menu.getByRole("menuitem", { name: /^Make a task…/ });
    await walk.state("08-answering-menu", { visible: [item.getByText("Available when the reply ends")], hidden: [] });
    await expect(item).toBeDisabled();
  }
});

test("a chat that cannot be read: loading, a failed read with Retry, and one that is gone", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const state = { listing: [] as Json[], detail: { [cid("a")]: chat() } as Record<string, Json> };
  const api = await chats(page, state);
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route(new RegExp(`/api/chats/atlas/${cid("a")}$`), async (route) => { await held; await route.fallback(); }, { times: 1 });
  await page.goto(`/projects/atlas/chats/${cid("a")}`);
  await walk.state("01-loading", { visible: [page.locator(".convo-skeleton")], hidden: [page.getByRole("textbox", { name: "Message the agent" })] });
  api.failing(true);
  release();
  // The read is tried three times before the page says so.
  await expect(page.getByText("Couldn’t load this chat.")).toBeVisible({ timeout: 15_000 });
  await walk.state("02-read-failed", { visible: [page.getByText("Couldn’t load this chat."), page.getByRole("button", { name: "Retry" })], hidden: [page.locator(".convo-skeleton")] });
  api.failing(false);
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText(/^The export loads every row’s comments/)).toBeVisible();

  await walk.open(`/projects/atlas/chats/${cid("9")}`);
  await walk.state("03-missing", { visible: [page.getByText("There is no such chat.")], hidden: [page.getByRole("textbox", { name: "Message the agent" }), page.getByRole("button", { name: "Chat actions" })] });
});

test("Monitor: a chat's running reply is a session row", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  await page.route("**/api/monitor", async (route) => {
    const response = await route.fetch();
    const json = await response.json() as { sessions: Json[] };
    json.sessions.push({ kind: "chat", project: "atlas", chat_id: cid("a"), name: "Why is the export slow?", session_id: "fixture-chat-session",
      state: "running", ...ENGINE, context_percent: 34, minutes: 2 });
    await route.fulfill({ response, json });
  });
  await page.goto("/monitor");
  const card = page.locator(".monitor-session").filter({ hasText: "Why is the export slow?" });
  await walk.state("01-monitor-chat", {
    action: () => card.scrollIntoViewIfNeeded(),
    visible: [card.getByText("Chat", { exact: true }), card.getByText("atlas / Why is the export slow?"), card.getByText(/context 34% · answering for 2 min$/)],
    hidden: [],
  });
});
