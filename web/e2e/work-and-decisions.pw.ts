import { test } from "./fixtures";
import { expect, type APIRequestContext, type Locator, type Page, type Route, type TestInfo } from "@playwright/test";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/**
 * Work panel and shared Needs you presentation states,
 * walked at 390 and 1440 over fictional stored tasks. Named HTTP overlays produce presentation and
 * transport states, intercepting writes in this spec. task-lifecycle.pw.ts covers actual persisted
 * decisions, messages and requested resumes through the same isolated service. The dedicated
 * conversation-decisions spec covers the anchored chat and cited-message resolution end to end.
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
      id: "walk-question",
      revision: 1,
      anchor_id: "walk-anchor",
      status: "open",
      audience: "operator",
      state: "blocked",
      resolution: null,
      since: minutesAgo(26),
      options: [
        { key: "A", label: "Fast-forward it", text: "Fast-forward it; the checkout is Altitude's own deployment copy." },
        { key: "B", label: "Keep failing closed", text: "Keep failing closed." },
      ],
      recommended_key: "A",
      recommendation: {
        text: "Fast-forward the owned deployment checkout.",
        label: "Fast-forward it",
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
    status: "open",
    audience: "operator",
    state: "blocked",
    resolution: null,
    since: minutesAgo(60 * 30),
    recommendation: null,
  };
}

interface Overlay {
  queue?: unknown[];
  wip?: Record<string, unknown>;
  overviewDelay?: number;
  overviewFail?: boolean;
  project?: (project: Record<string, unknown>) => Record<string, unknown>;
  projectDelay?: number;
  projectFail?: boolean;
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
    if (state.projectFail) return route.fulfill({ status: 503, json: { error: "Project read unavailable." } });
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
    const input = route.request().postDataJSON();
    return (answers.decideStatus ?? 200) === 200
      ? route.fulfill({ json: { question: { project: input.project, slug: input.slug, id: input.question_id, revision: input.revision,
        status: "open", audience: "operator", resolution: null,
        response: { text: "Use the chosen approach.", message_id: "walk-response", at: minutesAgo(0) } } } })
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

test("Work keeps waiting tasks once without answer controls, retains recent history, and recovers reads", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const { tasks } = await liveRows(request, project.name);
  const running = tasks.find((t) => t.state === "running")!;
  const base = await record(request, project.name, running.slug);
  const { decision, blocked } = dilemma(project.name, base);
  const queued: Row = { slug: "walk-queued", state: "queued", title: "Add the beta stage", updated: minutesAgo(5) };
  const waitsL3: Row = { slug: "walk-l3", state: "blocked", title: "Score pronunciation per phoneme", updated: minutesAgo(3), waiting_on: "l3", blocked_reason: "which suite?" };
  const parked: Row = { slug: "walk-parked", state: "blocked", title: "Wait for the landing window", block_actor: "altd", waiting_on: null };
  const stoppedTask: Row = { slug: "walk-stopped", state: "blocked", title: "Stopped validation", stop_id: "operator-stop" };
  const faulty: Row = { slug: "walk-fault", state: "blocked", title: "Repair checkout", fault: "checkout", blocked_reason: "Checkout unavailable." };
  const done: Row = { slug: "walk-done", state: "done", title: "Link task PR chips", updated: minutesAgo(90), prs: [208] };
  const state: Overlay = {
    queue: [decision],
    wip: { per_project: {}, machine: 1, waiting: [{ project: project.name, slug: queued.slug, why: "dispatch", hold: "WIP limit: 1 running on this machine" }] },
    project: (json) => ({ ...json, tasks: [...(json.tasks as Row[]).map((t) => t.slug === base.slug ? blocked : t), queued, waitsL3, parked, stoppedTask, faulty], archive: [done] }),
    task: { [base.slug]: blocked },
  };
  await overlay(page, project.name, state);
  let writes = 0;
  page.on("request", (req) => { if (req.method() === "POST") writes += 1; });
  const walk = walkthrough(page, info);
  const v = views(page, info);
  await walk.open(projectRoute(project.path, v.phone));
  await v.openPanel();
  const current = v.panel.getByRole("region", { name: "Current", exact: true });
  const waiting = current.getByRole("link", { name: startsWith(base.title as string) });
  const fold = v.panel.getByText(/^Done this week \(\d+\)$/);
  await expect(waiting).toHaveCount(1);
  await expect(waiting).toHaveAttribute("href", new RegExp(`question=${decision.id}&revision=1`));
  await expect(v.panel.getByRole("article")).toHaveCount(0);
  await expect(v.panel.getByRole("button", { name: "Fast-forward it", exact: true })).toHaveCount(0);
  const pausedRow = current.getByRole("link", { name: `${parked.title} · Work is paused; no reason is recorded.`, exact: true });
  const stoppedRow = current.getByRole("link", { name: `${stoppedTask.title} · You requested a stop; confirmation is in the task.`, exact: true });
  const faultRow = current.getByRole("link", { name: `${faulty.title} · A system problem paused work. Waiting for the coordinator to check the blocker.`, exact: true });
  await expect(pausedRow.locator(".dot")).toHaveAttribute("data-state", "idle");
  await expect(stoppedRow.locator(".dot")).toHaveAttribute("data-state", "danger");
  await expect(faultRow.locator(".dot")).toHaveAttribute("data-state", "danger");
  await expect(waiting.locator(".dot")).toHaveAttribute("data-state", "waiting");
  await expect(pausedRow).toHaveAttribute("href", `/projects/${project.name}/tasks/${parked.slug}`);
  await walk.state("01a-paused-stopped-fault-and-operator-answer", {
    visible: [pausedRow, stoppedRow, faultRow, waiting.getByText("Waiting for you", { exact: true })],
    hidden: [pausedRow.getByText(/Your turn|Stopped/)],
  });
  await walk.state("01-current-waiting-queued-and-l3", {
    visible: [waiting.getByText(/Your turn · 1 question/), current.getByRole("link", { name: startsWith(queued.title as string) }), current.getByRole("link", { name: startsWith(`${waitsL3.title} · Waiting for the coordinator`) }), fold],
    hidden: [v.panel.getByText(decision.question, { exact: true }), v.panel.getByRole("link", { name: /^Link task PR chips/ })],
  });
  await walk.state("02-recent-completion-expanded", {
    action: () => fold.click(), visible: [v.panel.getByRole("link", { name: "Link task PR chips · Done · PR #208 merged", exact: true })], hidden: [],
  });
  await walk.state("03-recent-completion-collapsed", {
    action: () => fold.click(), visible: [waiting], hidden: [v.panel.getByRole("link", { name: /^Link task PR chips/ })],
  });
  state.projectFail = true;
  state.overviewFail = true;
  const savedWork = v.panel.getByRole("alert").filter({ hasText: "Showing saved work." });
  const savedAttention = v.panel.getByRole("alert").filter({ hasText: "Attention status is saved" });
  await expect(savedWork).toBeVisible({ timeout: 40_000 });
  await expect(savedAttention).toBeVisible({ timeout: 40_000 });
  await walk.state("03b-cached-failure-retains-linked-rows", {
    visible: [waiting, savedWork, savedAttention, v.panel.getByText(/done this week · saved$/)],
    hidden: [v.panel.getByText("No current tasks. Ask L3 to start something.", { exact: true })],
  });
  state.projectFail = false;
  state.overviewFail = false;
  await savedWork.getByRole("button", { name: "Retry", exact: true }).click();
  await savedAttention.getByRole("button", { name: "Refresh", exact: true }).click();
  await walk.state("03c-cached-reads-recover", { visible: [waiting], hidden: [savedWork, savedAttention] });
  await clearRoutes(page);
  await overlay(page, project.name, { queue: [], project: (json) => ({ ...json, tasks: [], archive: [done] }) });
  await walk.open(projectRoute(project.path, v.phone));
  await v.openPanel();
  await walk.state("04-empty-current-retains-history", {
    visible: [v.panel.getByText("No current tasks. Ask L3 to start something.", { exact: true }), v.panel.getByText("0 current · 1 done this week", { exact: true }), fold], hidden: [current, pausedRow, stoppedRow, faultRow],
  });
  await clearRoutes(page);
  await overlay(page, project.name, { projectDelay: 4_000 });
  await walk.open(projectRoute(project.path, v.phone));
  await v.openPanel();
  await walk.state("05-loading-unknown-counts", {
    visible: [v.panel.getByLabel("Loading", { exact: true })], hidden: [current, v.panel.getByText(/^0 current/), page.getByText("Nothing needs you.", { exact: true })],
  });
  await clearRoutes(page);
  const failed: Overlay = { projectFail: true };
  await overlay(page, project.name, failed);
  await walk.open(projectRoute(project.path, v.phone));
  await v.openPanel();
  const error = v.panel.getByText(/Could not read the project's work/);
  await walk.state("06-initial-read-failed", {
    action: settles(error), visible: [error, v.panel.getByRole("button", { name: "Retry", exact: true })], hidden: [current, v.panel.getByText(/^0 current/)],
  });
  failed.projectFail = false;
  await walk.state("07-read-recovered", {
    action: () => v.panel.getByRole("button", { name: "Retry", exact: true }).click(), visible: [current.getByRole("link", { name: startsWith(base.title as string) })], hidden: [error],
  });
  await clearRoutes(page);
  await overlay(page, project.name, { overviewFail: true });
  await walk.open(projectRoute(project.path, v.phone));
  await v.openPanel();
  const attentionUnavailable = v.panel.getByRole("alert").filter({ hasText: "Attention status unavailable." });
  await walk.state("08-independent-tasks-survive-attention-read-failure", {
    action: settles(attentionUnavailable), visible: [attentionUnavailable, current.getByRole("link", { name: startsWith(base.title as string) }), page.locator('.badge[aria-label="Attention unavailable"]:visible')],
    hidden: [v.panel.getByText("No current tasks. Ask L3 to start something.", { exact: true })],
  });
  expect(writes, "Work is a read-only task overview").toBe(0);
});

test("Needs you: empty, recommendation and chat entry, sending, sent, failed, error, loading", async ({ page, request }, info) => {
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
  await walk.state("02-cards-overlay", {
    visible: [
      v.main.getByText(/^1 question · 1 stopped task/),
      card,
      card.getByText("L3 brought this to you", { exact: true }),
      v.main.getByRole("heading", { name: project.name, level: 2, exact: true }),
      card.getByText(decision.question, { exact: true }),
      card.getByRole("button", { name: "Fast-forward it", exact: true }),
      card.getByRole("link", { name: "Open L2 chat", exact: true }),
      v.card(second.title),
      v.card(second.title).getByText("Stopped mid-task", { exact: true }),
      v.main.getByText(/^That is everything/),
    ],
    hidden: [v.main.getByText("Nothing needs you.", { exact: true })],
  });
  await expect(card.getByLabel("Follow-ups", { exact: true })).toHaveCount(0);
  await expect(card.getByText("Why not keep failing closed for everything?")).toHaveCount(0);
  await expect(v.card(second.title).getByRole("button")).toHaveCount(0);
  state.overviewFail = true;
  const saved = v.main.getByRole("alert").filter({ hasText: "Showing saved questions." });
  await expect(saved).toBeVisible({ timeout: 40_000 });
  await expect(card.getByRole("button", { name: "Fast-forward it", exact: true })).toBeDisabled();
  await walk.state("03-saved-inbox-readonly", { visible: [saved, card], hidden: [v.main.getByText("Nothing needs you.", { exact: true })] });
  state.overviewFail = false;
  await saved.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(card.getByRole("button", { name: "Fast-forward it", exact: true })).toBeEnabled();
  await card.getByRole("button", { name: "Fast-forward it", exact: true }).click();
  await walk.state("04-sending-overlay", {
    action: () => card.getByRole("button", { name: "Send 1 answer", exact: true }).click(),
    visible: [card.getByRole("button", { name: "Sending…", disabled: true })],
    hidden: [],
  });
  const oneLeft = v.main.getByText(/^1 stopped task/);
  await walk.state("05-sent-card-gone-overlay", {
    action: settles(oneLeft),
    visible: [v.card(second.title), oneLeft],
    hidden: [card],
  });

  await clearRoutes(page);
  state.queue = [decision, second];
  await overlay(page, project.name, state);
  await interceptWrites(page, { decideStatus: 503 });
  await walk.open("/");
  await card.getByRole("button", { name: "Fast-forward it", exact: true }).click();
  await walk.state("06-send-failed-overlay", {
    action: () => card.getByRole("button", { name: "Send 1 answer", exact: true }).click(),
    visible: [card.getByRole("alert"), card.getByRole("button", { name: "Fast-forward it", exact: true })],
    hidden: [card.locator(".spinner")],
  });

  await clearRoutes(page);
  await overlay(page, project.name, state);
  await interceptWrites(page, { decideStatus: 403 });
  await walk.open("/");
  await card.getByRole("button", { name: "Fast-forward it", exact: true }).click();
  await card.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect(card.getByRole("button", { name: "Fast-forward it", exact: true })).toBeDisabled();
  await walk.state("06b-denied-retains-owner-and-response", {
    visible: [v.main.getByRole("heading", { name: project.name, level: 2, exact: true }), card.getByText(/You cannot send answers here/)], hidden: [],
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
