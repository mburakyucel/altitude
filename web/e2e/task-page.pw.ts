import { test } from "./fixtures";
import { expect, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";
import { DecisionSchema, TaskMessageSchema } from "../src/data/api";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/**
 * Task presentation states (SPEC.md §3.10) at both widths over fictional stored tasks. Named HTTP overlays
 * hold reads or display a block, fault, refusal, or missing session. task-lifecycle.pw.ts separately drives
 * real messages, resume requests, decisions, Stop and Reject through the isolated API and storage.
 */

type Row = Record<string, unknown> & { slug: string; title?: string; state?: string; session_id?: string };

async function projectRows(request: APIRequestContext, name: string): Promise<Row[]> {
  const response = await request.get(`/api/project/${encodeURIComponent(name)}`);
  expect(response.ok(), "Live project must be available").toBe(true);
  const project = await response.json();
  return [...project.tasks, ...project.archive] as Row[];
}

async function detail(request: APIRequestContext, name: string, slug: string): Promise<Row> {
  const response = await request.get(`/api/task/${encodeURIComponent(name)}/${encodeURIComponent(slug)}`);
  expect(response.ok(), `Task ${slug} must be readable`).toBe(true);
  return (await response.json()) as Row;
}

/** The first task in a state, with a full record; running tasks need a session to walk the transcript. */
async function taskIn(request: APIRequestContext, name: string, state: string): Promise<Row | undefined> {
  for (const row of await projectRows(request, name)) {
    if (row.state !== state) continue;
    const record = await detail(request, name, row.slug);
    if (state !== "running" || record.session_id) return record;
  }
  return undefined;
}

async function runningTask(request: APIRequestContext, name: string): Promise<Row> {
  const record = await taskIn(request, name, "running");
  expect(record, "The walkthrough needs one running task with a session").toBeTruthy();
  return record as Row;
}

const taskPath = (name: string, slug: string) => `/projects/${encodeURIComponent(name)}/tasks/${encodeURIComponent(slug)}`;
const apiTask = (name: string, slug: string) => `/api/task/${name}/${slug}`;

/** Serve one task record in place of the live one, and patch the overview around it. */
async function overlay(page: Page, name: string, record: Row, overview?: Record<string, unknown>) {
  const presentation = { ...record, steering: { state: record.state === "running" ? "running" : record.resume_after ? "resuming" : "idle", generation: record.agent_id ?? null, stop_id: null, error: null } };
  await page.route((url) => url.pathname === apiTask(name, record.slug), (route) => route.fulfill({ json: presentation }));
  if (overview) {
    await page.route((url) => url.pathname === "/api/overview", async (route) => {
      const response = await route.fetch();
      const json = (await response.json()) as Record<string, unknown>;
      await route.fulfill({ response, json: { ...json, ...overview } });
    });
  }
}

async function clearRoutes(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
}

/** The live session: the second tab on the phone, the panel on desktop (open by default at 1440). */
function views(page: Page, info: TestInfo) {
  const phone = info.project.name === "phone";
  const main = page.getByRole("main");
  return {
    phone,
    main,
    heading: (title: string) => page.getByRole("heading", { level: 1, name: title, exact: true }),
    composer: main.getByRole("textbox", { name: "Message the L2", exact: true }),
    conversation: main.getByRole("region", { name: "Task conversation", exact: true }),
    live: main.getByRole("region", { name: "Live session", exact: true }),
    stop: page.getByRole("button", { name: "Stop", exact: true }),
    reject: main.getByRole("button", { name: "Reject", exact: true }),
    stopConfirm: main.getByRole("group", { name: "Stop this task?", exact: true }),
    rejectConfirm: main.getByRole("group", { name: "Reject this task?", exact: true }),
    async showDetails() {
      if (phone) await page.getByRole("button", { name: /Task details$/ }).click();
    },
    async closeDetails() {
      if (phone) await page.getByRole("button", { name: "Close task details", exact: true }).click();
    },
    async showLive() {
      if (phone) await main.getByRole("link", { name: "Live session", exact: true }).click();
      else if ((await main.getByRole("button", { name: "Live session", exact: true }).getAttribute("aria-pressed")) !== "true") {
        await main.getByRole("button", { name: "Live session", exact: true }).click();
      }
    },
    async showConversation() {
      if (phone) await main.getByRole("link", { name: "Conversation", exact: true }).click();
    },
  };
}

test("a running task: conversation, live session, Raw events, accessible Stop and Reject confirmation", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const task = await runningTask(request, project.name);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const title = (task.title as string) || task.slug;
  const rawEvents = v.live.getByRole("button", { name: "Raw events", exact: true });
  const transcript = v.live.getByRole("region", { name: "Live transcript", exact: true });
  const raw = v.live.getByRole("region", { name: "Raw events", exact: true });

  await walk.open(taskPath(project.name, task.slug));
  await walk.state("01-running-conversation", {
    visible: [v.heading(title), v.main.getByText("L2 working", { exact: true }).first(), v.conversation, v.composer, v.stop, ...(v.phone ? [] : [v.reject])],
    hidden: [v.stopConfirm, v.rejectConfirm, v.main.getByLabel("Loading", { exact: true }), ...(v.phone ? [v.reject] : [])],
  });
  await walk.state("02-live-session-streaming", {
    action: () => v.showLive(),
    visible: [v.live, rawEvents, transcript, v.live.getByText("Following live · new steps appear at the bottom").last()],
    hidden: [v.live.getByLabel("Connecting", { exact: true }), raw, ...(v.phone ? [v.composer] : [])],
  });
  await walk.state("03-raw-events", { action: () => rawEvents.click(), visible: [raw], hidden: [transcript] });
  await walk.state("04-transcript-again", { action: () => rawEvents.click(), visible: [transcript], hidden: [raw] });
  if (!v.phone) {
    const toggle = v.main.getByRole("button", { name: "Live session", exact: true });
    await walk.state("05-panel-closed", { action: () => toggle.click(), visible: [v.conversation], hidden: [v.live] });
    await walk.state("06-panel-open", { action: () => toggle.click(), visible: [v.live, transcript], hidden: [] });
  }
  await walk.state("07-live-stop-accessible", {
    visible: [page.getByRole("button", { name: "Stop", exact: true })],
    hidden: [v.stopConfirm, v.rejectConfirm],
  });
  await walk.state("08-composer-stop-accessible", {
    action: () => v.showConversation(),
    visible: [v.stop],
    hidden: [v.stopConfirm],
  });
  await walk.state("09-reject-confirm", {
    action: async () => { await v.showDetails(); await v.reject.click(); },
    visible: [
      v.rejectConfirm,
      v.rejectConfirm.getByText("Reject this task? Its worker ends and the task is archived."),
      v.rejectConfirm.getByRole("textbox", { name: "Reason (optional)", exact: true }),
      v.rejectConfirm.getByRole("button", { name: "Reject task", exact: true }),
    ],
    hidden: [v.stopConfirm],
  });
  // Cancel comes first and takes focus, so Enter never rejects by accident.
  await expect(v.rejectConfirm.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
  await expect(v.rejectConfirm.getByRole("button")).toHaveText(["Cancel", "Reject task"]);
  await walk.state("10-reject-cancelled", {
    action: () => v.rejectConfirm.getByRole("button", { name: "Cancel", exact: true }).click(),
    visible: [v.reject],
    hidden: [v.rejectConfirm],
  });
  await walk.state("10b-reject-escaped", {
    action: async () => { await v.reject.click(); await expect(v.rejectConfirm).toBeVisible(); await page.keyboard.press("Escape"); },
    visible: [v.reject],
    hidden: [v.rejectConfirm],
  });
  expect((await (await request.get(`/api/task/${project.name}/${task.slug}`)).json()).state).toBe("running");
  await v.closeDetails();
  await expect(v.stop).toBeVisible();
});

test("a queued task says what it waits for; a held task reads as queued", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const base = await runningTask(request, project.name);
  // A real queued task when the service has one; otherwise a running record read as queued.
  const live = await taskIn(request, project.name, "queued");
  const queued: Row = live ?? { ...base, state: "queued", session_id: "", live: null, dispatched: null };
  info.annotations.push({ type: "queued data", description: live ? "live task" : "overlay on a running record" });
  if (!live) {
    await overlay(page, project.name, queued, {
      wip: { per_project: {}, machine: 0, waiting: [{ project: project.name, slug: queued.slug, why: "dispatch" }] },
    });
  }
  const title = (queued.title as string) || queued.slug;

  await walk.open(taskPath(project.name, queued.slug));
  await walk.state("01-queued", {
    action: () => v.showLive(),
    visible: [v.heading(title), v.main.getByText("Queued", { exact: true }).first(), v.live, v.live.getByText("Waits for dispatch"), ...(v.phone ? [] : [v.reject, v.composer])],
    hidden: [...(v.phone ? [v.composer] : []), v.stop, v.live.getByRole("button", { name: "Raw events", exact: true })],
  });
  await walk.state("01-queued-conversation", {
    action: () => v.showConversation(),
    visible: [v.composer],
    hidden: [v.stop],
  });

  await clearRoutes(page);
  const held: Row = {
    ...base,
    state: "blocked",
    live: null,
    resume_after: "2026-09-08T02:00:00+00:00",
    blocked_reason: "usage limit: the window resets at 02:00",
  };
  await overlay(page, project.name, held);
  await walk.open(taskPath(project.name, held.slug));
  await walk.state("02-held-reads-as-queued", {
    action: () => v.showLive(),
    visible: [v.main.getByText("Waiting to resume", { exact: true }).first(), v.live.getByText("Waits for resume")],
    hidden: [v.main.getByText("Blocked", { exact: true }), v.stop],
  });
  await walk.state("03-held-composer", {
    action: () => v.showConversation(),
    visible: [v.composer, ...(v.phone ? [] : [v.main.getByText("Delivered when Altitude resumes the L2.")])],
    hidden: [],
  });
});

test("a long L3 coordination message folds to its summary line and opens in place", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const base = await runningTask(request, project.name);
  const at = new Date().toISOString();
  // A fictional equivalent of a long grant-coordination message: ids, times, constraints and instructions.
  const coordination = "Sandbox grant re-recorded at 2026-01-05 10:00 UTC using the operator's earlier answer "
    + "0a1b2c3d4e5f60718293a4b5c6d7e8f9, question 9f8e7d6c5b4a39281706f5e4d3c2b1a0 r1. Resolve 1234abcd5678ef90 against this "
    + "confirmation and run the bounded make sandbox-check path, then review/check/land this increment under existing delivery "
    + "rules. Keep the grant through its verification and landing; purpose grants cover relevant iteration, so do not revoke "
    + "between steps or re-ask the operator for this unchanged purpose. Revoke when this increment's approved sandbox work is "
    + "complete. Offline guest tests, 2 CPU/4 GiB, disposable overlay, fixture engines and no host service changes remain.";
  const summary = "Grant re-recorded; run the sandbox check and keep the grant through landing";
  const rows = [
    TaskMessageSchema.parse({ id: "coord-0", at, role: "l3", by: "l3", text: "Saved before summaries: continue from the brief." }),
    TaskMessageSchema.parse({ id: "coord-1", at, role: "operator", text: "Please finish the sandbox check." }),
    TaskMessageSchema.parse({ id: "coord-2", at, role: "l3", by: "l3", text: coordination, summary }),
    TaskMessageSchema.parse({ id: "coord-3", at, role: "l2", by: "l2", text: "Running the sandbox check now; I will report when it lands." }),
  ];
  await overlay(page, project.name, { ...base, messages: rows });
  await walk.open(taskPath(project.name, base.slug));
  await v.showConversation();
  const [legacy, row] = [v.conversation.locator('[data-role="l3"]').first(), v.conversation.locator('[data-role="l3"]').last()];
  const show = row.getByRole("button", { name: "Show", exact: true });
  const hide = row.getByRole("button", { name: "Hide", exact: true });
  const original = row.getByText(/Sandbox grant re-recorded/);
  await walk.state("01-coordination-folded", {
    visible: [row.getByText(`L3 · ${summary}`, { exact: true }), show, legacy.getByText("L3 messaged the L2", { exact: true }),
      v.conversation.getByText("Please finish the sandbox check."), v.conversation.getByText(/Running the sandbox check now/), v.composer],
    hidden: [original, hide, legacy.getByText(/Saved before summaries/)],
  });
  // The summary wraps on a phone rather than being cut; it stays a compact row either way.
  expect((await row.boundingBox())!.height).toBeLessThan(v.phone ? 80 : 60);
  await walk.state("02-coordination-open", {
    action: () => show.click(),
    visible: [original, hide, row.getByText(`L3 · ${summary}`, { exact: true }), v.conversation.getByText(/Running the sandbox check now/)],
    hidden: [show],
  });
  await expect(hide).toHaveAttribute("aria-expanded", "true");
  await walk.state("03-coordination-folded-again", {
    action: () => hide.click(),
    visible: [show, v.composer],
    hidden: [original, hide],
  });
  await walk.state("04-unsummarised-open", {
    action: () => legacy.getByRole("button", { name: "Show", exact: true }).click(),
    visible: [legacy.getByText("L3 messaged the L2", { exact: true }), legacy.getByText("Saved before summaries: continue from the brief.")],
    hidden: [original],
  });
});

test("a blocked task: the question at the end of the chat, waiting for L3, a fault", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const base = await runningTask(request, project.name);
  const title = (base.title as string) || base.slug;
  const line = v.main.locator(".task-line");

  const question = "Should the timer keep the old default?";
  const asked = new Date().toISOString();
  const decision = DecisionSchema.parse({
    project: project.name, slug: base.slug, title, kind: "asks", asked_by: "l3",
    id: "timer-question", revision: 1, anchor_id: "timer-anchor", status: "open", audience: "operator",
    state: "blocked", question, detail: question, asked,
    recommendation: { text: "Keep the old timer default.", label: "Keep it & resume", why: "The old default is what the tests cover." },
    resolution: null,
  });
  const anchor = TaskMessageSchema.parse({ id: decision.anchor_id, at: asked, role: "l3", by: "l3", text: question });
  await overlay(
    page,
    project.name,
    { ...base, state: "blocked", live: null, resume_after: null, blocked_reason: question,
      question: decision, questions: [decision], question_group: { id: decision.id, revision: 1, anchor_id: anchor.id, questions: [decision] },
      messages: [...(base.messages as unknown[]), anchor, { ...anchor, id: "timer-later", role: "l2", by: "l2", text: "I also checked the timer tests." }] },
    { queue: [decision] },
  );
  await walk.open(`${taskPath(project.name, base.slug)}?question=${decision.id}&revision=${decision.revision}`);
  const card = v.conversation.locator(`[data-question-id="${decision.id}"]`);
  await expect(card).toBeInViewport();
  const turn = page.locator(`.conversation-question:has([data-question-id="${decision.id}"])`);
  await expect(turn).toBeFocused();
  // The open question is the last thing in the chat, after later messages.
  await expect(page.locator(".msg-row:has-text('I also checked the timer tests.') ~ .conversation-question")).toHaveCount(1);
  await walk.state("01-blocked-on-the-operator", {
    visible: [v.main.getByText("Your turn · 1 question", { exact: true }).first(), turn.getByText("Your turn · 1 question", { exact: true }), card, card.getByText(question), card.getByRole("button", { name: "Keep it & resume", exact: true }), v.composer, ...(v.phone ? [] : [v.reject])],
    hidden: [v.stop, v.main.getByRole("button", { name: "Resume", exact: true })],
  });
  await expect(line).toHaveText("Waiting for your answer to the task’s question.");

  await clearRoutes(page);
  const waiting = DecisionSchema.parse({ ...decision, id: "suite-question", anchor_id: "suite-anchor", asked_by: "l2", audience: "l3", question: "which suite covers the timer", recommendation: null });
  const waitingAnchor = TaskMessageSchema.parse({ ...anchor, id: waiting.anchor_id, role: "l2", by: "l2", text: waiting.question });
  await overlay(page, project.name, { ...base, state: "blocked", live: null, resume_after: null, waiting_on: "l3", blocked_reason: waiting.question,
    question: waiting, questions: [waiting], question_group: { id: waiting.id, revision: 1, anchor_id: waitingAnchor.id, questions: [waiting] }, messages: [...(base.messages as unknown[]), waitingAnchor] }, { queue: [] });
  await walk.open(`${taskPath(project.name, base.slug)}?question=${waiting.id}&revision=1`);
  const l3Question = v.conversation.locator(`[data-question-id="${waiting.id}"]`);
  await walk.state("02-blocked-waiting-for-l3", {
    visible: [v.main.getByText("Waiting for coordinator", { exact: true }).first(), v.conversation.getByText("L3 is answering", { exact: true }), l3Question.getByText("which suite covers the timer", { exact: true }), v.composer],
    hidden: [card, l3Question.getByRole("button"), v.main.getByRole("button", { name: "Resume", exact: true })],
  });

  await clearRoutes(page);
  await overlay(page, project.name, {
    ...base,
    state: "blocked",
    live: null,
    resume_after: null,
    waiting_on: "l3",
    fault: "sandbox",
    blocked_reason: "The sandbox refused the network socket. Tried twice with the bundled browser.",
    question: null,
    questions: [],
  });
  await walk.open(taskPath(project.name, base.slug));
  await walk.state("03-blocked-by-a-fault", {
    visible: [line.getByText("A system problem paused work. Waiting for the coordinator to check the blocker.")],
    hidden: [v.main.getByText("Tried twice"), card],
  });
  await walk.state("04-fault-session-paused", {
    action: () => v.showLive(),
    visible: [v.live.getByText("Session paused until the task resumes", { exact: true })],
    hidden: [],
  });
});

test("done and rejected tasks read read-only, the PR in the header", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const done = await taskIn(request, project.name, "done");
  expect(done, "The walkthrough needs one done task").toBeTruthy();
  const doneTitle = (done!.title as string) || done!.slug;
  const prs = (done!["prs"] as number[] | undefined) ?? [];
  const pr = prs[prs.length - 1];

  await walk.open(taskPath(project.name, done!.slug));
  await walk.state("01-done", {
    visible: [v.heading(doneTitle), v.main.getByText("Done", { exact: true }).first(), ...(!v.phone && pr ? [v.main.locator(".task-chips").getByText(`PR #${pr} merged`, { exact: false })] : [])],
    hidden: [v.composer, v.stop, v.reject],
  });
  await walk.state("02-done-session-ended", {
    action: () => v.showLive(),
    // The footer is the panel's last line; a transcript's tool output may quote the same words.
    visible: [v.live, v.live.getByText("Session ended").or(v.live.getByText("No session file for this attempt")).last()],
    hidden: [v.live.getByLabel("Connecting", { exact: true }), v.live.getByRole("button", { name: "Pause", exact: true })],
  });

  // The header's PR chip with failed checks and a merge hold: a report shape the archive rarely keeps.
  await clearRoutes(page);
  const number = pr ?? 1;
  const repository = "https://github.com/example/project";
  const held = {
    ...done!,
    prs: [number],
    hold_merge: "review before merge",
    report_json: { landed: { prs: [{ number, merged: false }], main_runs: [{ conclusion: "failure" }] } },
  };
  await overlay(page, project.name, { ...held, repository });
  await walk.open(taskPath(project.name, done!.slug));
  const prLink = v.main.getByRole("link", { name: `PR #${number} open · main checks failed` });
  await walk.state("03-pr-open-checks-failed-and-held", {
    action: () => v.showDetails(),
    visible: [prLink, v.main.getByText("Merge held", { exact: true }).first()],
    hidden: [],
  });
  await expect(prLink).toHaveAttribute("href", `${repository}/pull/${number}`);
  await expect(prLink).toHaveAttribute("target", "_blank");
  await expect(prLink).toHaveAttribute("rel", "noopener noreferrer");
  await clearRoutes(page);
  await overlay(page, project.name, { ...held, repository: null });
  await walk.open(taskPath(project.name, done!.slug));
  await walk.state("03b-pr-without-repository", {
    action: () => v.showDetails(),
    visible: [v.main.getByText(`PR #${number} open · main checks failed`, { exact: false })],
    hidden: [prLink],
  });

  await clearRoutes(page);
  const rejected = await taskIn(request, project.name, "rejected");
  expect(rejected, "The walkthrough needs one rejected task").toBeTruthy();
  const rejectedTitle = (rejected!.title as string) || rejected!.slug;
  await walk.open(taskPath(project.name, rejected!.slug));
  await walk.state("04-rejected", {
    visible: [v.heading(rejectedTitle), v.main.getByText("Rejected", { exact: true }).first(), v.conversation],
    hidden: [v.composer, v.stop, v.reject],
  });
});

test("a message shows at once, then Not sent. Retry when the server refuses it", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const task = await runningTask(request, project.name);
  const text = "UI walkthrough: this message never reaches the service";
  const sent: Row[] = [];
  // Every send is intercepted: the first refused, the retry accepted and kept on the record.
  let refuse = true;
  await page.route((url) => url.pathname === "/api/l2/message", async (route) => {
    if (refuse) return route.fulfill({ status: 409, json: { error: "no active session" } });
    const body = route.request().postDataJSON() as { text: string; request_id: string };
    // The stored row carries the submission id, as the server does; that id settles the pending bubble.
    const message = { id: body.request_id, at: new Date().toISOString(), role: "operator", text: body.text };
    sent.push(message);
    return route.fulfill({ json: { ok: true, message } });
  });
  await page.route((url) => url.pathname === apiTask(project.name, task.slug), async (route) => {
    const response = await route.fetch();
    const json = (await response.json()) as Row;
    await route.fulfill({ response, json: { ...json, messages: [...((json["messages"] as Row[]) ?? []), ...sent] } });
  });
  const alert = v.main.getByRole("alert").filter({ hasText: "Not sent." });
  const retry = alert.getByRole("button", { name: "Retry", exact: true });

  await walk.open(taskPath(project.name, task.slug));
  await walk.state("01-composer", { visible: [v.composer], hidden: [alert] });
  await walk.state("02-not-sent-retry", {
    action: async () => {
      await v.composer.fill(text);
      await v.main.getByRole("button", { name: "Send", exact: true }).click();
    },
    visible: [alert, retry, v.composer],
    hidden: [v.conversation.locator(".bubble", { hasText: text })],
  });
  await expect(v.composer).toHaveValue(text);
  await expect(v.composer).toBeFocused();
  refuse = false;
  await walk.state("03-sent", {
    action: () => retry.click(),
    visible: [v.conversation.locator(".bubble", { hasText: text })],
    hidden: [alert],
  });
  await expect(v.composer).toHaveValue("");
  // The accepted send invalidates the task query; finish its route before page teardown disposes it.
  await page.unrouteAll({ behavior: "wait" });
});

test("loading, a failed read with Retry, and an empty conversation", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const task = await runningTask(request, project.name);
  const title = (task.title as string) || task.slug;
  const isTask = (url: URL) => url.pathname === apiTask(project.name, task.slug);

  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route(isTask, async (route) => {
    await gate;
    await route.continue();
  });
  await walk.open(taskPath(project.name, task.slug));
  await walk.state("01-loading", { visible: [v.main.getByLabel("Loading", { exact: true })], hidden: [v.heading(title)] });
  release();
  await walk.state("02-loaded", { visible: [v.heading(title)], hidden: [v.main.getByLabel("Loading", { exact: true })] });

  await clearRoutes(page);
  let fail = true;
  await page.route(isTask, (route) => (fail ? route.fulfill({ status: 500, json: { error: "boom" } }) : route.continue()));
  await walk.open(taskPath(project.name, task.slug));
  const error = v.main.getByText("Could not load the task.");
  await walk.state("03-error-retry", {
    // Three retries with backoff come first (TanStack Query's default); the error shows after them.
    action: () => error.waitFor({ timeout: 30_000 }),
    visible: [error, v.main.getByRole("button", { name: "Retry", exact: true })],
    hidden: [v.heading(title)],
  });
  fail = false;
  await walk.state("04-recovered", {
    action: () => v.main.getByRole("button", { name: "Retry", exact: true }).click(),
    visible: [v.heading(title)],
    hidden: [error],
  });

  await clearRoutes(page);
  await overlay(page, project.name, { ...task, messages: [] });
  await walk.open(taskPath(project.name, task.slug));
  await walk.state("05-empty-conversation", { visible: [v.conversation.getByText("No messages yet.")], hidden: [] });
});

test("the live session: connecting, unavailable, no session file", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const task = await runningTask(request, project.name);
  const isTranscript = (url: URL) => url.pathname === `/api/transcript/${project.name}/${task.slug}`;
  const unavailable = v.live.getByText("No session file for this attempt");

  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route(isTranscript, async (route) => {
    await gate;
    await route.continue();
  });
  await walk.open(taskPath(project.name, task.slug));
  await walk.state("01-connecting", {
    action: () => v.showLive(),
    visible: [v.live.getByLabel("Connecting", { exact: true })],
    hidden: [v.live.getByRole("region", { name: "Live transcript", exact: true })],
  });
  release();
  await walk.state("02-streaming", {
    visible: [v.live.getByRole("region", { name: "Live transcript", exact: true })],
    hidden: [v.live.getByLabel("Connecting", { exact: true })],
  });

  await clearRoutes(page);
  await page.route(isTranscript, (route) => route.fulfill({ status: 404, json: { error: "transcript unavailable for this task generation" } }));
  await walk.open(taskPath(project.name, task.slug));
  await walk.state("03-unavailable", {
    action: () => v.showLive(),
    visible: [unavailable],
    hidden: [v.live.getByLabel("Connecting", { exact: true })],
  });

  await clearRoutes(page);
  await overlay(page, project.name, { ...task, session_id: "" });
  await walk.open(taskPath(project.name, task.slug));
  await walk.state("04-no-session-file", {
    action: () => v.showLive(),
    visible: [unavailable],
    hidden: [v.live.getByRole("button", { name: "Raw events", exact: true })],
  });
});
