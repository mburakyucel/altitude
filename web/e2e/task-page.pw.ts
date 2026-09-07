import { expect, test, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";
import { TaskMessageSchema } from "../src/data/api";
import { liveProject } from "./live-data";
import { walkthrough } from "./walkthrough";

/**
 * The task page (SPEC.md §3.10), every state walked at 390 and 1440. Real tasks carry the running, queued,
 * done and rejected states; the states the live service cannot be asked to produce (a block, a fault, a
 * refused message, a failed read, a gone session) are overlaid on real records with page.route, so nothing
 * here dispatches, stops, rejects, or messages a real task.
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
  await page.route((url) => url.pathname === apiTask(name, record.slug), (route) => route.fulfill({ json: record }));
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
    stop: main.getByRole("button", { name: "Stop", exact: true }),
    reject: main.getByRole("button", { name: "Reject", exact: true }),
    stopConfirm: main.getByRole("group", { name: "Stop this task?", exact: true }),
    rejectConfirm: main.getByRole("group", { name: "Reject this task?", exact: true }),
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

test("a running task: conversation, live session, Raw events, Stop and Reject confirms", async ({ page, request }, info) => {
  const project = await liveProject(request);
  const task = await runningTask(request, project.name);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const title = (task.title as string) || task.slug;
  const rawEvents = v.live.getByRole("button", { name: "Raw events", exact: true });
  const transcript = v.live.getByRole("region", { name: "Live transcript", exact: true });
  const raw = v.live.getByRole("region", { name: "Raw events", exact: true });

  await walk.open(taskPath(project.name, task.slug));
  await walk.state("01-running-conversation", {
    visible: [v.heading(title), v.main.getByText("Running", { exact: true }).first(), v.conversation, v.composer, v.stop, v.reject],
    hidden: [v.stopConfirm, v.rejectConfirm, v.main.getByLabel("Loading", { exact: true })],
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
  await walk.state("07-stop-confirm", {
    action: () => v.stop.click(),
    visible: [v.stopConfirm, v.stopConfirm.getByText("Stop this task? Its worker ends; the branch stays.")],
    hidden: [v.rejectConfirm],
  });
  await walk.state("08-stop-cancelled", {
    action: () => v.stopConfirm.getByRole("button", { name: "Cancel", exact: true }).click(),
    visible: [v.stop, v.reject],
    hidden: [v.stopConfirm],
  });
  await walk.state("09-reject-confirm", {
    action: () => v.reject.click(),
    visible: [
      v.rejectConfirm,
      v.rejectConfirm.getByText("Reject this task? Its worker ends and the task is archived."),
      v.rejectConfirm.getByRole("textbox", { name: "Reason (optional)", exact: true }),
    ],
    hidden: [v.stopConfirm],
  });
  await walk.state("10-reject-cancelled", {
    action: () => v.rejectConfirm.getByRole("button", { name: "Cancel", exact: true }).click(),
    visible: [v.stop, v.reject],
    hidden: [v.rejectConfirm],
  });
});

test("a queued task says what it waits for; a held task reads as queued", async ({ page, request }, info) => {
  const project = await liveProject(request);
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
    visible: [v.heading(title), v.main.getByText("Queued", { exact: true }).first(), v.live, v.live.getByText("Waits for dispatch"), v.reject],
    hidden: [v.composer, v.stop, v.live.getByRole("button", { name: "Raw events", exact: true })],
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
    visible: [v.main.getByText("Queued", { exact: true }).first(), v.live.getByText("Waits for resume · usage limit: the window resets at 02:00")],
    hidden: [v.main.getByText("Blocked", { exact: true }), v.stop],
  });
  await walk.state("03-held-composer", {
    action: () => v.showConversation(),
    visible: [v.composer, v.main.getByText("Delivered when Altitude resumes the L2.")],
    hidden: [],
  });
});

test("a blocked task: the decision card, waiting for L3, a fault", async ({ page, request }, info) => {
  const project = await liveProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const base = await runningTask(request, project.name);
  const title = (base.title as string) || base.slug;
  const line = v.main.locator(".task-line");

  const question = "Should the timer keep the old default?";
  await overlay(
    page,
    project.name,
    { ...base, state: "blocked", live: null, resume_after: null, blocked_reason: question },
    { queue: [{ project: project.name, slug: base.slug, kind: "decision", title, question, asked: new Date().toISOString(), options: ["Keep it", "Change it"] }] },
  );
  await walk.open(taskPath(project.name, base.slug));
  const card = v.conversation.getByRole("article", { name: title, exact: true });
  await walk.state("01-blocked-on-the-operator", {
    visible: [v.main.getByText("Blocked", { exact: true }).first(), card, card.getByText(question), v.composer, v.reject],
    hidden: [line, v.stop],
  });

  await clearRoutes(page);
  await overlay(page, project.name, { ...base, state: "blocked", live: null, resume_after: null, waiting_on: "l3", blocked_reason: "which suite covers the timer" });
  await walk.open(taskPath(project.name, base.slug));
  await walk.state("02-blocked-waiting-for-l3", {
    visible: [line.getByText("Waits for L3's answer · which suite covers the timer"), v.composer],
    hidden: [card],
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
  });
  await walk.open(taskPath(project.name, base.slug));
  await walk.state("03-blocked-by-a-fault", {
    visible: [line.getByText("The sandbox refused the network socket. L3 has been told.")],
    hidden: [v.main.getByText("Tried twice"), card],
  });
  await walk.state("04-fault-session-paused", {
    action: () => v.showLive(),
    visible: [v.live.getByText("Session paused until the task resumes")],
    hidden: [],
  });
});

test("done and rejected tasks read read-only, the PR in the header", async ({ page, request }, info) => {
  const project = await liveProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const done = await taskIn(request, project.name, "done");
  expect(done, "The walkthrough needs one done task").toBeTruthy();
  const doneTitle = (done!.title as string) || done!.slug;
  const prs = (done!["prs"] as number[] | undefined) ?? [];
  const pr = prs[prs.length - 1];

  await walk.open(taskPath(project.name, done!.slug));
  await walk.state("01-done", {
    visible: [v.heading(doneTitle), v.main.getByText("Done", { exact: true }).first(), ...(pr ? [v.main.locator(".task-chips, .task-state-line").getByText(`PR #${pr} merged`, { exact: false })] : [])],
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
  await overlay(page, project.name, {
    ...done!,
    prs: [number],
    hold_merge: "review before merge",
    report_json: { landed: { prs: [{ number, merged: false }], main_runs: [{ conclusion: "failure" }] } },
  });
  const repository = "https://github.com/example/project";
  let projectRepository: string | null = repository;
  await page.route((url) => url.pathname === `/api/project/${encodeURIComponent(project.name)}`, async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), repository: projectRepository } });
  });
  await walk.open(taskPath(project.name, done!.slug));
  const prLink = v.main.getByRole("link", { name: `PR #${number} open · main checks failed` });
  await walk.state("03-pr-open-checks-failed-and-held", {
    visible: [prLink, v.main.getByText("Merge held · review before merge")],
    hidden: [],
  });
  await expect(prLink).toHaveAttribute("href", `${repository}/pull/${number}`);
  await expect(prLink).toHaveAttribute("target", "_blank");
  await expect(prLink).toHaveAttribute("rel", "noopener noreferrer");
  projectRepository = null;
  await walk.open(taskPath(project.name, done!.slug));
  await walk.state("03b-pr-without-repository", {
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
  const project = await liveProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const task = await runningTask(request, project.name);
  const text = "UI walkthrough: this message never reaches the service";
  const sent: Row[] = [];
  // Every send is intercepted: the first refused, the retry accepted and kept on the record.
  let refuse = true;
  await page.route((url) => url.pathname === "/api/l2/message", async (route) => {
    if (refuse) return route.fulfill({ status: 409, json: { error: "no active session" } });
    const body = route.request().postDataJSON() as { text: string };
    const role = TaskMessageSchema.shape.role.options.find((role) => role !== "l2" && role !== "l3");
    const message = { id: `ui-${sent.length + 1}`, at: new Date().toISOString(), role, text: body.text };
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
  refuse = false;
  await walk.state("03-sent", {
    action: () => retry.click(),
    visible: [v.conversation.locator(".bubble", { hasText: text })],
    hidden: [alert],
  });
  await expect(v.composer).toHaveValue("");
});

test("loading, a failed read with Retry, and an empty conversation", async ({ page, request }, info) => {
  const project = await liveProject(request);
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
  const project = await liveProject(request);
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
