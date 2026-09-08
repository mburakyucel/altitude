import { test } from "./fixtures";
import { expect, type APIRequestContext, type Locator, type Page, type Route, type TestInfo } from "@playwright/test";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/**
 * Slice 3 (SPEC.md §3.5 task card, §3.7 work panel, §3.8 decision card, §3.9 decision page), every state
 * walked at 390 and 1440 over fictional stored tasks. Named HTTP overlays produce presentation and
 * transport states, intercepting writes in this spec. task-lifecycle.pw.ts covers actual persisted
 * decisions, messages and requested resumes through the same isolated service.
 */

type Row = Record<string, unknown> & { slug: string; title?: string; state?: string };

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const startsWith = (text: string) => new RegExp(`^${text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`);

async function liveRows(request: APIRequestContext, name: string): Promise<{ tasks: Row[]; archive: Row[]; repository: string | null }> {
  const response = await request.get(`/api/project/${encodeURIComponent(name)}`);
  expect(response.ok(), "Live project must be available").toBe(true);
  const project = await response.json();
  return { tasks: project.tasks as Row[], archive: (project.archive ?? []) as Row[], repository: project.repository ?? null };
}

async function record(request: APIRequestContext, name: string, slug: string): Promise<Row> {
  const response = await request.get(`/api/task/${encodeURIComponent(name)}/${encodeURIComponent(slug)}`);
  expect(response.ok(), `Task ${slug} must be readable`).toBe(true);
  return (await response.json()) as Row;
}

/** The decision the walk overlays: an L2's question L3 escalated, on a real task blocked in the record. */
function dilemma(name: string, task: Row) {
  const question = "When the deployment checkout is behind origin/main at dispatch, should Altitude fast-forward it or keep failing closed?";
  return {
    decision: {
      project: name,
      slug: task.slug,
      title: task.title || task.slug,
      kind: "asks",
      asked_by: "l3",
      question,
      detail: `${question} Option A: Fast-forward it; the checkout is Altitude's own deployment copy. Option B: Keep failing closed. I recommend A.`,
      asked: minutesAgo(25),
      since: minutesAgo(26),
      options: [
        { key: "A", label: "Fast-forward it", text: "Fast-forward it; the checkout is Altitude's own deployment copy." },
        { key: "B", label: "Keep failing closed", text: "Keep failing closed." },
      ],
      recommendation: {
        option: "A",
        why: "The checkout is Altitude's own deployment copy, so the move is a pure fast-forward with nothing local to lose. The race has blocked three dispatches this week.",
      },
    },
    blocked: {
      ...task,
      state: "blocked",
      waiting_on: "burak",
      blocked_reason: "Dispatch found the deployment checkout two commits behind origin/main. The brief says fail closed. Should I fast-forward instead?",
      resume_after: null,
      fault: null,
      prs: [140],
      messages: [],
      events: [
        ...((task.events as Row[] | undefined) ?? []).filter((e) => typeof e.at === "string" && (e.at as string) < minutesAgo(30)),
        { at: minutesAgo(26), kind: "state", from: "running", to: "blocked", by: "l2", reason: "Dispatch found the deployment checkout two commits behind origin/main. The brief says fail closed. Should I fast-forward instead?" },
        { at: minutesAgo(25), kind: "escalated", by: "l3", question: `${question} Option A: Fast-forward it. Option B: Keep failing closed. I recommend A.` },
      ],
    } as Row,
  };
}

/** A second decision for Needs you: a task stopped mid-task in another project of the same service. */
function stopped(name: string, task: Row) {
  return {
    project: name,
    slug: task.slug,
    title: task.title || task.slug,
    kind: "stopped",
    asked_by: "l2",
    question: "the recording upload fails at 10 minutes",
    detail: "the recording upload fails at 10 minutes",
    asked: minutesAgo(60 * 30),
    since: minutesAgo(60 * 30),
    options: [
      { key: "resume", label: "Resume" },
      { key: "reject", label: "Reject" },
    ],
    recommendation: { option: "resume", why: "" },
  };
}

interface Overlay {
  queue?: unknown[];
  wip?: Record<string, unknown>;
  overviewDelay?: number;
  overviewFail?: boolean;
  project?: (project: Record<string, unknown>) => Record<string, unknown>;
  projectDelay?: number;
  task?: Record<string, Row | null>;
  taskFail?: boolean;
  chat?: (view: Record<string, unknown>) => Record<string, unknown>;
}

/** Fetch the live answer and fulfil the route with a patched body. A delayed handler (the loading states)
 * can outlive its test; its rejected route.fetch would otherwise be charged to the next test in the worker
 * and end it (full `make ui` run of 2026-09-07: the Needs you walk died in its error state both widths). */
async function serve(route: Route, patch: (json: Record<string, unknown>) => Record<string, unknown>) {
  let response: Awaited<ReturnType<Route["fetch"]>>;
  try {
    response = await route.fetch();
  } catch {
    return route.abort().catch(() => undefined);
  }
  const json = (await response.json()) as Record<string, unknown>;
  return route.fulfill({ response, json: patch(json) }).catch(() => undefined);
}

/** Serve the live reads with the overlay's changes; the overlay object is read on every request, so a walk can mutate it. */
async function overlay(page: Page, name: string, state: Overlay) {
  await page.route((url) => url.pathname === "/api/overview", async (route) => {
    if (state.overviewDelay) await new Promise((resolve) => setTimeout(resolve, state.overviewDelay));
    if (state.overviewFail) return route.fulfill({ status: 503, json: { error: "altd is restarting" } });
    return serve(route, (json) => ({ ...json, ...(state.queue ? { queue: state.queue } : {}), ...(state.wip ? { wip: state.wip } : {}) }));
  });
  await page.route((url) => url.pathname === `/api/project/${name}`, async (route) => {
    if (state.projectDelay) await new Promise((resolve) => setTimeout(resolve, state.projectDelay));
    return serve(route, (json) => (state.project ? state.project(json) : json));
  });
  await page.route((url) => url.pathname.startsWith(`/api/task/${name}/`), async (route, request) => {
    const slug = decodeURIComponent(new URL(request.url()).pathname.split("/").pop() ?? "");
    if (state.taskFail) return route.fulfill({ status: 500, json: { error: "state file unreadable" } });
    const patched = state.task?.[slug];
    if (patched) return route.fulfill({ json: patched });
    return route.continue();
  });
  await page.route((url) => url.pathname === `/api/chat/${name}`, async (route) => serve(route, (json) => (state.chat ? state.chat(json) : json)));
}

/** Refuse every write the walk could trigger, answering as the server would; the walk never changes a real task. */
async function interceptWrites(page: Page, answers: { decide?: () => Promise<unknown> | unknown; decideStatus?: number; chat?: (route: Route) => Promise<void>; l2?: () => unknown }) {
  await page.route((url) => url.pathname === "/api/decide", async (route) => {
    await answers.decide?.();
    return (answers.decideStatus ?? 200) === 200
      ? route.fulfill({ json: { ok: true, queued: true } })
      : route.fulfill({ status: answers.decideStatus, json: { error: "only blocked tasks need a user decision" } });
  });
  await page.route((url) => url.pathname === "/api/chat", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    if (answers.chat) return answers.chat(route);
    return route.fulfill({ status: 500, json: { error: "the walkthrough sends no chat" } });
  });
  await page.route((url) => url.pathname === "/api/l2/message", async (route) => {
    await answers.l2?.();
    return route.fulfill({ json: { ok: true, message: { id: "walk-1", at: minutesAgo(0), role: "burak", text: "x" } } });
  });
  await page.route((url) => url.pathname === "/api/task/action", (route) => route.fulfill({ status: 500, json: { error: "the walkthrough acts on no task" } }));
}

async function clearRoutes(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
}

function views(page: Page, info: TestInfo) {
  const phone = info.project.name === "phone";
  const main = page.getByRole("main");
  const panel = main.getByRole("region", { name: "Work", exact: true }).or(page.getByRole("dialog", { name: "Work", exact: true }));
  return {
    phone,
    main,
    panel,
    card: (title: string, within: Locator = main) => within.getByRole("article", { name: title, exact: true }),
    async openPanel() {
      if (phone) return;
      const toggle = main.getByRole("button", { name: "Work panel", exact: true });
      if ((await toggle.count()) > 0 && (await toggle.getAttribute("aria-pressed")) !== "true") await toggle.click();
    },
  };
}

const projectRoute = (path: string, phone: boolean) => (phone ? `${path}?tab=work` : path);

// An error state arrives after TanStack Query's three retries (about 7 s); several reloads per walk.
test.describe.configure({ timeout: 180_000 });
// The same failure, closed at the source: no handler survives its test.
test.afterEach(async ({ page }) => page.unrouteAll({ behavior: "ignoreErrors" }));
const settles = (locator: Locator) => () => expect(locator).toBeVisible({ timeout: 30_000 });

test("the work panel: live sections, the queued hold, Waits for L3, the fold, deciding, decided, failed, empty, loading", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const { tasks, archive } = await liveRows(request, project.name);
  const running = tasks.find((t) => t.state === "running");
  expect(running, "The walkthrough needs one running task").toBeTruthy();
  const base = await record(request, project.name, (running as Row).slug);
  const walk = walkthrough(page, info);
  const v = views(page, info);

  await walk.open(projectRoute(project.path, v.phone));
  await v.openPanel();
  await walk.state("01-panel-live", {
    visible: [v.panel, v.panel.getByRole("heading", { name: "Work", exact: true }), v.panel.getByText(/^\d+ active · \d+ done this week$/), v.panel.getByRole("link", { name: startsWith((base.title as string) || base.slug) })],
    hidden: [v.panel.getByLabel("Loading", { exact: true })],
  });
  info.annotations.push({ type: "live rows", description: `${tasks.length} active, ${archive.length} archived` });

  // The overlay: one decision on the running task, one queued task held by the WIP limit, one task
  // waiting on L3, and one task done this week with a merged PR.
  const { decision, blocked } = dilemma(project.name, base);
  const queued: Row = { slug: "walk-queued", state: "queued", title: "Add the beta stage", updated: minutesAgo(5) };
  const waitsL3: Row = { slug: "walk-l3", state: "blocked", title: "Score pronunciation per phoneme", updated: minutesAgo(3), waiting_on: "l3", blocked_reason: "which suite?" };
  const done: Row = { slug: "walk-done", state: "done", title: "Link task PR chips", updated: minutesAgo(90), prs: [208] };
  const state: Overlay = {
    queue: [decision],
    wip: { per_project: {}, machine: 1, waiting: [{ project: project.name, slug: queued.slug, why: "dispatch", hold: `WIP limit 1 reached for ${project.name} (1 running)` }] },
    project: (json) => ({ ...json, tasks: [...(json.tasks as Row[]).map((t) => (t.slug === base.slug ? { ...t, state: "blocked", waiting_on: "burak" } : t)), queued, waitsL3], archive: [done] }),
    task: { [base.slug]: blocked },
  };
  await clearRoutes(page);
  await overlay(page, project.name, state);
  let decided = 0;
  await interceptWrites(page, {
    decide: async () => {
      decided += 1;
      await new Promise((resolve) => setTimeout(resolve, 1_200));
      state.queue = [];
      state.project = (json) => ({ ...json, tasks: [...(json.tasks as Row[]), queued, waitsL3], archive: [done] });
    },
  });
  await walk.open(projectRoute(project.path, v.phone));
  await v.openPanel();
  const title = (base.title as string) || base.slug;
  const card = v.card(title, v.panel);
  const active = v.panel.getByRole("region", { name: "Active", exact: true });
  const fold = v.panel.getByText(/^Done this week \(\d+\)$/);
  await walk.state("02-panel-sections-overlay", {
    visible: [
      v.panel.getByRole("heading", { name: /^Needs you/ }),
      card,
      card.getByText("L3 asks", { exact: true }),
      card.getByRole("button", { name: "Fast-forward it", exact: true }),
      active.getByRole("link", { name: `${queued.title} · Queued · waits for a slot · WIP limit 1 reached for ${project.name} (1 running)`, exact: true }),
      active.getByRole("link", { name: `${waitsL3.title} · Waits for L3`, exact: true }),
      fold,
    ],
    hidden: [active.getByRole("link", { name: startsWith(title) }), v.panel.getByRole("link", { name: /^Link task PR chips/ })],
  });
  await walk.state("03-done-fold-open-overlay", {
    action: () => fold.click(),
    visible: [v.panel.getByRole("link", { name: "Link task PR chips · Done · PR #208 merged", exact: true })],
    hidden: [],
  });
  await walk.state("04-deciding-overlay", {
    action: () => card.getByRole("button", { name: "Fast-forward it", exact: true }).click(),
    visible: [card.locator(".spinner"), card.getByRole("button", { name: "Keep failing closed", disabled: true })],
    hidden: [],
  });
  const movedRow = active.getByRole("link", { name: startsWith(`${title} · `) });
  await walk.state("05-decided-task-moved-overlay", {
    action: settles(movedRow),
    visible: [movedRow],
    hidden: [card, v.panel.getByRole("heading", { name: /^Needs you/ })],
  });
  expect(decided, "one decide call, intercepted").toBe(1);

  await clearRoutes(page);
  state.queue = [decision];
  state.project = (json) => ({ ...json, tasks: (json.tasks as Row[]).map((t) => (t.slug === base.slug ? { ...t, state: "blocked", waiting_on: "burak" } : t)) });
  await overlay(page, project.name, state);
  await interceptWrites(page, { decideStatus: 409 });
  await walk.open(projectRoute(project.path, v.phone));
  await v.openPanel();
  await walk.state("06-decide-failed-overlay", {
    action: () => card.getByRole("button", { name: "Keep failing closed", exact: true }).click(),
    visible: [card.getByText("Could not record the decision."), card.getByRole("button", { name: "Retry", exact: true }), card.getByRole("button", { name: "Fast-forward it", exact: true })],
    hidden: [card.locator(".spinner")],
  });

  await clearRoutes(page);
  await overlay(page, project.name, { queue: [], project: (json) => ({ ...json, tasks: [], archive: [] }) });
  await walk.open(projectRoute(project.path, v.phone));
  await v.openPanel();
  await walk.state("07-empty-overlay", {
    visible: [v.panel.getByText("Nothing running. Ask L3 for something.", { exact: true }), v.panel.getByText("0 active · 0 done this week", { exact: true })],
    hidden: [fold],
  });

  await clearRoutes(page);
  await overlay(page, project.name, { projectDelay: 4_000 });
  await walk.open(projectRoute(project.path, v.phone));
  await v.openPanel();
  await walk.state("08-loading-overlay", {
    visible: [v.panel.getByLabel("Loading", { exact: true })],
    hidden: [v.panel.getByRole("region", { name: "Active", exact: true })],
  });
});

test("Needs you: empty, the cards with follow-ups, deciding, decided, failed, error, loading", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const { tasks } = await liveRows(request, project.name);
  const running = tasks.find((t) => t.state === "running");
  expect(running, "The walkthrough needs one running task").toBeTruthy();
  const base = await record(request, project.name, (running as Row).slug);
  const other = tasks.find((t) => t.slug !== base.slug) ?? base;
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const { decision, blocked } = dilemma(project.name, base);
  const second = stopped(project.name, other);
  const title = decision.title;

  await walk.open("/");
  await walk.state("01-empty-live", {
    visible: [v.main.getByRole("heading", { name: "Needs you", exact: true }), v.main.getByText("Nothing needs you.", { exact: true })],
    hidden: [v.main.getByLabel("Loading", { exact: true }), v.main.getByText(/^That is everything/)],
  });

  const followUps = [
    { at: minutesAgo(20), role: "user", text: "Why not keep failing closed for everything?", trigger: "chat", turn_id: "walk-c1", slug: base.slug },
    { at: minutesAgo(19), role: "assistant", text: "Fail-closed protects checkouts Altitude does not own; this one it owns.", trigger: "chat", turn_id: "walk-c1", slug: base.slug },
  ];
  const state: Overlay = {
    queue: [decision, second],
    task: { [base.slug]: blocked },
    chat: (view) => ({ ...view, history: [...((view.history as unknown[]) ?? []), ...followUps], queued: [{ id: "walk-q1", at: minutesAgo(1), text: "And on a fresh install?", trigger: "chat", slug: base.slug }] }),
  };
  await clearRoutes(page);
  await overlay(page, project.name, state);
  await interceptWrites(page, {
    decide: async () => {
      await new Promise((resolve) => setTimeout(resolve, 1_200));
      state.queue = [second];
    },
  });
  await walk.open("/");
  const card = v.card(title);
  const thread = card.getByLabel("Follow-ups", { exact: true });
  await walk.state("02-cards-overlay", {
    visible: [
      v.main.getByText(/^2 things wait on you/),
      card,
      card.getByText("L3 asks", { exact: true }),
      card.locator(".chip", { hasText: project.name }),
      card.getByText(decision.question, { exact: true }),
      card.getByRole("button", { name: "Fast-forward it", exact: true }),
      card.getByRole("link", { name: "More context", exact: true }),
      v.card(second.title),
      v.card(second.title).getByText("Stopped mid-task", { exact: true }),
      v.main.getByText(/^That is everything/),
    ],
    hidden: [v.main.getByText("Nothing needs you.", { exact: true })],
  });
  await walk.state("03-follow-ups-mirrored-overlay", {
    visible: [
      thread.getByText("Why not keep failing closed for everything?"),
      thread.getByText("Fail-closed protects checkouts Altitude does not own; this one it owns."),
      thread.getByText("And on a fresh install?"),
      thread.getByText("· queued for L3"),
    ],
    hidden: [],
  });
  await walk.state("04-deciding-overlay", {
    action: () => card.getByRole("button", { name: "Fast-forward it", exact: true }).click(),
    visible: [card.locator(".spinner"), card.getByRole("button", { name: "Keep failing closed", disabled: true })],
    hidden: [],
  });
  const oneLeft = v.main.getByText(/^One thing waits on you/);
  await walk.state("05-decided-card-gone-overlay", {
    action: settles(oneLeft),
    visible: [v.card(second.title), oneLeft],
    hidden: [card],
  });

  await clearRoutes(page);
  state.queue = [decision, second];
  await overlay(page, project.name, state);
  await interceptWrites(page, { decideStatus: 409 });
  await walk.open("/");
  await walk.state("06-decide-failed-overlay", {
    action: () => card.getByRole("button", { name: "Keep failing closed", exact: true }).click(),
    visible: [card.getByText("Could not record the decision."), card.getByRole("button", { name: "Retry", exact: true })],
    hidden: [card.locator(".spinner")],
  });

  await clearRoutes(page);
  await overlay(page, project.name, { overviewFail: true });
  await walk.open("/");
  const needsError = v.main.getByText(/^Could not read what needs you\./);
  await walk.state("07-error-overlay", {
    action: settles(needsError),
    visible: [needsError, v.main.getByRole("button", { name: "Retry", exact: true })],
    hidden: [v.main.getByLabel("Loading", { exact: true })],
  });

  await clearRoutes(page);
  await overlay(page, project.name, { overviewDelay: 4_000 });
  await walk.open("/");
  await walk.state("08-loading-overlay", {
    visible: [v.main.getByLabel("Loading", { exact: true })],
    hidden: [v.main.getByText("Nothing needs you.", { exact: true })],
  });
});

test("the decision page: ready, follow-up in flight and answered, to the L2, deciding, decided, gone, error, loading", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const { tasks, repository } = await liveRows(request, project.name);
  const running = tasks.find((t) => t.state === "running");
  expect(running, "The walkthrough needs one running task").toBeTruthy();
  const base = await record(request, project.name, (running as Row).slug);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const { decision, blocked } = dilemma(project.name, base);
  const route = `/projects/${encodeURIComponent(project.name)}/decisions/${encodeURIComponent(base.slug)}`;
  const taskPath = `/projects/${encodeURIComponent(project.name)}/tasks/${encodeURIComponent(base.slug)}`;

  const state: Overlay = { queue: [decision], task: { [base.slug]: blocked } };
  await overlay(page, project.name, state);
  let l2Sent = 0;
  await interceptWrites(page, {
    decide: async () => {
      await new Promise((resolve) => setTimeout(resolve, 1_200));
      state.queue = [];
      state.task = { [base.slug]: { ...blocked, state: "running", decision: { key: "A", option: "Fast-forward it", note: "Only Altitude's own checkout.", at: minutesAgo(0), by: "burak" }, events: [...(blocked.events as Row[]), { at: minutesAgo(0), kind: "decided", key: "A", option: "Fast-forward it", note: "Only Altitude's own checkout.", by: "burak" }] } };
    },
    chat: async (route) => {
      const body = route.request().postDataJSON() as { text: string; slug?: string };
      expect(body.slug, "a follow-up to L3 carries the decision's slug").toBe(base.slug);
      await new Promise((resolve) => setTimeout(resolve, 2_500));
      state.chat = (view) => ({
        ...view,
        history: [
          ...((view.history as unknown[]) ?? []),
          { at: minutesAgo(0), role: "user", text: body.text, trigger: "chat", turn_id: "walk-c2", slug: base.slug },
          { at: minutesAgo(0), role: "assistant", text: "Nothing local is lost: the checkout is Altitude's own deployment copy.", trigger: "chat", turn_id: "walk-c2", slug: base.slug },
        ],
      });
      const lines = ['{"turn":{"id":"walk-c2","started_at":"2026-09-07T09:14:00+00:00","trigger":"chat","slug":"' + base.slug + '"}}', '{"t":"Nothing local is lost."}', '{"done":{"turn_id":"walk-c2"}}'];
      return route.fulfill({ status: 200, contentType: "application/x-ndjson", body: `${lines.join("\n")}\n` });
    },
    l2: () => {
      l2Sent += 1;
      state.task = { [base.slug]: { ...blocked, messages: [{ id: "walk-m1", at: minutesAgo(0), role: "burak", text: "Is anything uncommitted there?" }], events: [...(blocked.events as Row[]), { at: minutesAgo(0), kind: "task-message", message_id: "walk-m1", role: "burak", by: "burak" }] } };
    },
  });

  // Desktop opens from the project (the crumb leads back there); the phone opens from Needs you.
  if (v.phone) {
    await walk.open("/");
    await v.card(decision.title).getByRole("link", { name: "More context", exact: true }).click();
  } else {
    await walk.open(route);
  }
  const composer = v.main.getByRole("textbox", { name: "Ask a follow-up", exact: true });
  const recipient = v.main.getByRole("combobox", { name: "Recipient", exact: true });
  const timeline = v.main.getByRole("list", { name: "Where this came from", exact: true });
  const evidence = v.main.getByRole("region", { name: "Evidence", exact: true });
  const options = v.main.locator(".decision-options-page");
  const col = v.main.locator(".decision-col");
  await walk.state("01-ready-overlay", {
    visible: [
      v.main.getByRole("heading", { level: 1, name: decision.question, exact: true }),
      page.getByRole("link", { name: "Open task", exact: true }),
      ...(v.phone ? [page.getByRole("button", { name: "Back", exact: true }), page.getByRole("heading", { level: 1, name: "Needs you", exact: true })] : [v.main.getByRole("link", { name: `‹ ${project.name}`, exact: true })]),
      v.main.locator(".decision-chips").getByText("L3 asks", { exact: true }),
      options.getByRole("button", { name: "Fast-forward it", exact: true }),
      options.getByRole("button", { name: "Keep failing closed", exact: true }),
      options.getByPlaceholder("Add a note for the L2 (optional)"),
      v.main.getByRole("heading", { name: "Why L3 recommends Fast-forward it", exact: true }),
      col.getByText(/^The checkout is Altitude's own deployment copy/),
      timeline.getByText("asked L3", { exact: false }),
      timeline.getByText("Dispatch found the deployment checkout two commits behind origin/main.", { exact: false }),
      timeline.getByText(/^L3 escalated to you: /),
      timeline.getByText("The task is blocked until you choose.", { exact: true }),
      evidence.getByRole("link", { name: "Task conversation", exact: true }),
      evidence.getByRole("link", { name: "Live session at the failing step", exact: true }),
      ...(repository ? [evidence.getByRole("link", { name: "PR #140", exact: true })] : []),
      composer,
      recipient,
      v.main.getByText("Your question and the answer appear here and on the card. The L2 stays blocked until you choose.", { exact: true }),
      ...(v.phone ? [] : [v.panel, v.card(decision.title, v.panel)]),
    ],
    hidden: [v.main.getByRole("status"), v.main.getByLabel("Loading", { exact: true })],
  });
  if (!v.phone) await expect(v.card(decision.title, v.panel)).toHaveAttribute("data-selected", "true");
  await expect(page.getByRole("link", { name: "Open task", exact: true })).toHaveAttribute("href", taskPath);

  await walk.state("02-follow-up-in-flight-overlay", {
    action: async () => {
      await recipient.selectOption("l3");
      await composer.fill("Why not keep failing closed for everything?");
      await v.main.getByRole("button", { name: "Send", exact: true }).click();
    },
    visible: [v.main.getByText("L3 is answering…", { exact: true }), timeline.getByText("Why not keep failing closed for everything?")],
    hidden: [timeline.getByText("L3 answered", { exact: false })],
  });
  await walk.state("03-follow-up-answered-overlay", {
    visible: [timeline.getByText("L3 answered", { exact: false }), timeline.getByText("Nothing local is lost: the checkout is Altitude's own deployment copy.")],
    hidden: [v.main.getByText("L3 is answering…", { exact: true })],
  });
  await walk.state("04-follow-up-to-the-l2-overlay", {
    action: async () => {
      await recipient.selectOption("l2");
      await composer.fill("Is anything uncommitted there?");
      await v.main.getByRole("button", { name: "Send", exact: true }).click();
    },
    visible: [timeline.getByText("asked the L2", { exact: false }), timeline.getByText("Is anything uncommitted there?")],
    hidden: [v.main.getByText("L3 is answering…", { exact: true })],
  });
  expect(l2Sent, "one task message, intercepted").toBe(1);

  await walk.state("05-deciding-overlay", {
    action: async () => {
      await options.getByPlaceholder("Add a note for the L2 (optional)").fill("Only Altitude's own checkout.");
      await options.getByRole("button", { name: "Fast-forward it", exact: true }).click();
    },
    visible: [options.locator(".spinner"), options.getByRole("button", { name: "Keep failing closed", disabled: true })],
    hidden: [],
  });
  const decidedBanner = v.main.getByRole("status").filter({ hasText: "Decided just now: Fast-forward it · Only Altitude's own checkout." });
  await walk.state("06-decided-overlay", {
    // The banner follows the decide's reply and two live re-reads through the overlay.
    action: settles(decidedBanner),
    visible: [decidedBanner, timeline.getByText("You chose Fast-forward it", { exact: false })],
    hidden: [options, composer, timeline.getByText("The task is blocked until you choose.", { exact: true })],
  });

  await clearRoutes(page);
  await overlay(page, project.name, { queue: [], task: { [base.slug]: { ...blocked, state: "done", events: [...(blocked.events as Row[]), { at: minutesAgo(1), kind: "state", from: "blocked", to: "done", by: "altd" }] } } });
  await walk.open(route);
  await walk.state("07-task-gone-overlay", {
    visible: [v.main.getByRole("status").filter({ hasText: "This task was done." }), v.main.getByRole("link", { name: "Open the archived task", exact: true })],
    hidden: [options, composer],
  });

  await clearRoutes(page);
  await overlay(page, project.name, { queue: [], taskFail: true });
  await walk.open(route);
  const decisionError = v.main.getByText(/^Could not read the decision\./);
  await walk.state("08-error-overlay", {
    action: settles(decisionError),
    visible: [decisionError, v.main.getByRole("button", { name: "Retry", exact: true })],
    hidden: [options, composer],
  });

  await clearRoutes(page);
  await overlay(page, project.name, { overviewDelay: 4_000 });
  await walk.open(route);
  await walk.state("09-loading-overlay", {
    // The page column's skeleton; on desktop the work panel beside it may still be loading too.
    visible: [v.main.getByLabel("Loading", { exact: true }).first()],
    hidden: [options, composer],
  });
});
