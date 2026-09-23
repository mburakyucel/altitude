import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "conversation-decisions-service.py" });
test.setTimeout(90_000);

type Question = { id: string; revision: number; anchor_id: string; status: string; question: string; audience: string; options: { key: string; label: string; text: string }[]; recommended_key: string | null; recommendation: { text: string; label: string } | null; response: { text: string; at: string; message_id: string; option_key?: string } | null; resolution: { disposition: string; message_id: string; text: string; by: string; l3_authority?: string; recorded_by?: string; recorded_attempt?: number; option_key?: string } | null };
type Group = { id: string; revision: number; anchor_id: string; questions: Question[] };
type Task = { state: string; session_id: string; agent_id: string; hold_merge: string | null; question: Question | null; questions: Question[]; question_group: Group; messages: { id: string; text: string; role: string; question_refs?: { id: string; revision: number }[] }[] };
const taskPath = (slug: string) => `/projects/atlas/tasks/${slug}`;
async function readTask(request: APIRequestContext, slug: string): Promise<Task> {
  const response = await request.get(`/api/task/atlas/${slug}`);
  expect(response.ok()).toBe(true);
  return response.json();
}
async function queue(request: APIRequestContext) {
  return (await (await request.get("/api/overview")).json()).queue as { slug: string }[];
}
const atQuestion = (slug: string, q: Question) => `${taskPath(slug)}?question=${q.id}&revision=${q.revision}`;
const questionCard = (page: Page, q: Question) => page.getByRole("region", { name: "Task conversation", exact: true })
  .locator(`[data-question-id="${q.id}"][data-question-revision="${q.revision}"]`);
const groupCard = (page: Page, group: Group) => page.getByRole("region", { name: "Task conversation", exact: true })
  .locator(`[data-group-id="${group.id}"]`).filter({ has: page.locator(`[data-question-id="${group.questions[0]!.id}"][data-question-revision="${group.questions[0]!.revision}"]`) });
async function createGroup(request: APIRequestContext) {
  const response = await request.post("/fixture/group");
  expect(response.ok()).toBe(true);
  const { slug } = await response.json() as { slug: string };
  return { slug, initial: await readTask(request, slug) };
}
async function send(page: Page, text: string) {
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  await page.getByRole("textbox", { name: "Message the L2", exact: true }).fill(text);
  const saved = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/l2/message" && response.request().method() === "POST");
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  expect((await saved).ok(), "The pending bubble is not evidence of a saved message").toBe(true);
  await expect(conversation.locator(".bubble").filter({ hasText: text }).last()).toBeVisible();
}
async function modelCheckpoint(request: APIRequestContext, slug: string) {
  const response = await request.post("/fixture/checkpoint", { data: { slug } });
  expect(response.ok()).toBe(true);
  return response.json() as Promise<{ message_id: string; answer: string }>;
}
/** A sent reply hands the turn back: the open group becomes one quiet line at the end of the chat. */
async function handedBack(page: Page, card: Locator, line = "Sent · the L2 has your reply.") {
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  await expect(conversation.getByRole("status").filter({ hasText: line })).toBeVisible();
  await expect(card).toHaveCount(0);
}
/** The owner parks again; questions it still needs return to the operator as asked again. */
async function park(request: APIRequestContext, slug: string) {
  const response = await request.post("/fixture/park", { data: { slug } });
  expect(response.ok()).toBe(true);
  const group = await response.json() as Group;
  expect(group.questions.filter((q) => q.status === "open").every((q) => (q as Question & { asked_again?: boolean }).asked_again)).toBe(true);
  return group;
}
const turnLabel = (page: Page) => page.getByRole("region", { name: "Task conversation", exact: true }).locator(".conversation-turn");
async function submit(page: Page, request: APIRequestContext, slug: string, resolve = true) {
  const saved = page.waitForResponse((row) => row.url().endsWith("/api/decide"));
  await page.getByRole("button", { name: /^Send \d+ answers?$/ }).click();
  expect((await saved).ok()).toBe(true);
  if (resolve) {
    const task = await readTask(request, slug);
    const submitted = task.question_group.questions.filter((q) => q.response && q.status === "open");
    for (const question of submitted) expect(question.resolution).toBeNull();
    await modelCheckpoint(request, slug);
    const interpreted = await readTask(request, slug);
    for (const question of submitted) {
      expect(interpreted.questions.find((q) => q.id === question.id && q.revision === question.revision)?.resolution)
        .toMatchObject({ disposition: "answered", message_id: question.response!.message_id,
          by: interpreted.messages.find((message) => message.id === question.response!.message_id)!.role });
    }
  }
}
async function recorded(card: Locator, disposition = "answered") {
  await expect(card).toHaveAttribute("data-status", "resolved");
  await expect(card.getByText(disposition === "answered" ? "Decision recorded" : "Question closed", { exact: true })).toBeVisible();
  await expect(card.getByRole("button")).toHaveCount(0);
}

// No route overlays: browser → real Handler/storage/wake → deterministic owner response.
test("early owner withdrawal preserves independent answers and work, then reasks unchanged and revised decisions", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const created = await request.post("/fixture/freshness");
  expect(created.ok()).toBe(true);
  const { slug } = await created.json() as { slug: string };
  const initial = await readTask(request, slug);
  const [review, retention] = initial.question_group.questions as [Question, Question];
  const list = page.getByRole("article", { name: "Review revised rollout", exact: true });
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const withdraw = async (remaining: boolean) => {
    await send(page, "Audit the rollout wording before merge.");
    await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
    await modelCheckpoint(request, slug);
    if (remaining) await park(request, slug);
  };
  const ready = async (changed = false) => {
    const response = await request.post("/fixture/freshness-ready", { data: { slug, changed } });
    expect(response.ok()).toBe(true);
    return (await readTask(request, slug)).question_group.questions.find((q) => q.status === "open" && q.question.startsWith("Merge"))!;
  };
  await walk.open("/");
  await walk.state("01-fresh-review-and-independent-question", {
    visible: [list.getByRole("button", { name: "Merge rollout", exact: true }), list.getByRole("button", { name: "14 days", exact: true })], hidden: [],
  });
  await list.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  await send(page, "What does the rollback choice cover?");
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  await modelCheckpoint(request, slug);
  expect((await readTask(request, slug)).question_group.questions.map((q) => [q.id, q.revision, q.status]))
    .toEqual(initial.question_group.questions.map((q) => [q.id, q.revision, q.status]));
  await walk.open("/");
  await walk.state("02-harmless-followup-hands-turn-back", { visible: [page.getByRole("heading", { name: "Needs you", exact: true })], hidden: [list] });
  await park(request, slug);
  await page.reload();
  await walk.state("02b-asked-again-keeps-fresh-actions", {
    visible: [list.getByRole("button", { name: "Merge rollout", exact: true }), list.getByRole("button", { name: "14 days", exact: true })],
    hidden: [list.getByText("Discussion in progress · decision still open", { exact: true })],
  });
  await list.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  await withdraw(true);
  await page.reload();
  const old = questionCard(page, review);
  const reason = "I withdrew the merge question while I assess the requested audit. Your rollback choice remains useful.";
  await old.scrollIntoViewIfNeeded();
  await walk.state("03-withdrawn-question-is-a-compact-audit-row", {
    visible: [old.getByText("Question withdrawn", { exact: true }), questionCard(page, retention).getByRole("button", { name: "14 days", exact: true })],
    hidden: [old.getByText(review.question, { exact: true }), old.getByText(reason, { exact: true }), old.getByRole("button"), old.getByText("Recommended:", { exact: true })],
  });
  expect((await old.boundingBox())!.height).toBeLessThan(48);
  await old.getByText("Question withdrawn", { exact: true }).click();
  await walk.state("04-expand-withdrawn-question-to-read-history", {
    visible: [old.getByText(review.question, { exact: true }), old.getByText(reason, { exact: true }), old.getByText("Recommended:", { exact: true })],
    hidden: [old.getByRole("button"), old.getByText("Earlier recommendation", { exact: true })],
  });
  await old.getByText("Question withdrawn", { exact: true }).click();
  await walk.state("04b-collapse-history-leaves-independent-answer-visible", {
    visible: [old.getByText("Question withdrawn", { exact: true }), questionCard(page, retention).getByRole("button", { name: "14 days", exact: true })],
    hidden: [old.getByText(reason, { exact: true })],
  });
  const stale = await request.post("/api/decide", { data: { project: "atlas", slug, question_id: review.id, revision: review.revision, option_key: "merge" } });
  expect(stale.status()).toBe(409);
  await walk.open("/");
  await walk.state("05-needs-you-keeps-only-independent-question", {
    visible: [list.getByRole("button", { name: "14 days", exact: true })],
    hidden: [list.getByRole("button", { name: "Merge rollout", exact: true }), list.getByText(review.question, { exact: true })],
  });
  await list.getByRole("button", { name: "14 days", exact: true }).click();
  await submit(page, request, slug);
  await expect(list).toBeHidden();
  const chosen = await readTask(request, slug);
  expect(chosen.questions.find((q) => q.id === retention.id && q.revision === retention.revision)?.resolution).toMatchObject({ disposition: "answered", text: "Keep the old index for fourteen days." });
  expect(chosen.hold_merge).toBe(initial.hold_merge);
  expect(chosen.session_id).toBe(initial.session_id);
  const work = await request.post("/fixture/freshness-work", { data: { slug } });
  expect((await work.json()).notes).toBe("Requested wording audit remains required.\n");
  await walk.open(atQuestion(slug, retention));
  await walk.state("06-answer-applies-only-to-rollback-window", {
    visible: [conversation.getByText("Questions closed", { exact: true }), questionCard(page, retention).getByText("Decision recorded", { exact: true }), questionCard(page, retention).getByText("Keep the old index for fourteen days.", { exact: true })],
    hidden: [conversation.getByText("Answers recorded", { exact: true }), conversation.getByRole("button", { name: "Merge rollout", exact: true })],
  });
  const unchanged = await ready();
  expect(unchanged.question).toBe(review.question);
  expect(unchanged.id).not.toBe(review.id);
  await walk.open(atQuestion(slug, unchanged));
  await walk.state("07-same-question-ready-again-with-fresh-action", {
    visible: [questionCard(page, unchanged).getByRole("button", { name: "Merge rollout", exact: true })], hidden: [old.getByRole("button")],
  });
  await withdraw(false);
  await page.reload();
  const withdrawnAgain = questionCard(page, unchanged);
  const closedGroup = withdrawnAgain.locator("xpath=ancestor::div[contains(@class, 'conversation-question')]");
  await expect(closedGroup).toHaveAttribute("data-historical", "true");
  await expect(closedGroup).toHaveCSS("outline-style", "none");
  await walk.state("07b-withdrawn-only-group-leaves-a-quiet-audit-row", {
    visible: [withdrawnAgain.getByText("Question withdrawn", { exact: true })],
    hidden: [withdrawnAgain.getByText(unchanged.question, { exact: true }), closedGroup.getByText("L2", { exact: true }), withdrawnAgain.getByRole("button")],
  });
  await withdrawnAgain.locator("summary").focus();
  await expect(withdrawnAgain.locator("summary")).toHaveCSS("outline-style", "solid");
  await withdrawnAgain.locator("summary").press("Enter");
  await expect(withdrawnAgain.getByText(unchanged.question, { exact: true })).toBeVisible();
  const revised = await ready(true);
  expect(revised.id).not.toBe(unchanged.id);
  expect(revised.question).toBe("Merge the revised rollout?");
  await walk.open("/");
  await walk.state("08-revised-work-ready-for-decision", {
    visible: [list.getByText(revised.question, { exact: true }), list.getByRole("button", { name: "Merge rollout", exact: true })],
    hidden: [list.getByText(review.question, { exact: true }), list.getByRole("button", { name: "14 days", exact: true })],
  });
  await walk.open(taskPath(slug));
  await old.scrollIntoViewIfNeeded();
  await walk.state("08b-withdrawn-history-stays-compact-after-reasking", {
    visible: [old.getByText("Question withdrawn", { exact: true })],
    hidden: [old.getByText(reason, { exact: true }), old.getByText(review.question, { exact: true })],
  });
  await old.getByText("Question withdrawn", { exact: true }).click();
  await expect(old.getByText(reason, { exact: true })).toBeVisible();
  await walk.open(atQuestion(slug, review));
  await expect(old.getByText(reason, { exact: true })).toBeHidden();
  await old.getByText("Question withdrawn", { exact: true }).click();
  await walk.state("09-old-link-keeps-withdrawn-history", {
    visible: [old.getByText("Question withdrawn", { exact: true }), old.getByText(reason, { exact: true })], hidden: [old.getByRole("button")],
  });
  const final = await readTask(request, slug);
  expect(final.hold_merge).toBe(initial.hold_merge);
  expect(final.questions.filter((q) => q.resolution?.disposition === "withdrawn")).toHaveLength(2);
  expect(final.question_group.questions.filter((q) => q.status === "open").map((q) => q.id)).toEqual([revised.id]);
  expect(final.messages.filter((row) => row.text === "Audit the rollout wording before merge.")).toHaveLength(2);
});

test("owner records delegated L3 authority while operator decisions and independent blocked work remain", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const created = await request.post("/fixture/delegated-questions");
  expect(created.ok()).toBe(true);
  const { slug } = await created.json() as { slug: string };
  const initial = await readTask(request, slug);
  const [lease, policy] = initial.question_group.questions as [Question, Question];
  const listCard = page.getByRole("article", { name: "Lease and policy", exact: true });
  const initialOverview = await (await request.get("/api/overview")).json();
  expect((await queue(request)).filter((row) => row.slug === slug)).toHaveLength(2);
  await walk.open("/");
  await walk.state("01-unnecessary-escalation-and-operator-choice", {
    visible: [listCard.getByText(lease.question, { exact: true }), listCard.getByText(policy.question, { exact: true })], hidden: [],
  });
  expect((await request.post("/fixture/l3-followup", { data: { slug } })).ok()).toBe(true);
  await page.reload();
  await walk.state("02-l3-recommendation-leaves-both-open", {
    visible: [listCard.getByText(lease.question, { exact: true }), listCard.getByText(policy.question, { exact: true })], hidden: [],
  });
  expect((await queue(request)).filter((row) => row.slug === slug)).toHaveLength(2);
  const settledResponse = await request.post("/fixture/l3-settle", { data: { slug } });
  expect(settledResponse.ok()).toBe(true);
  const settled = await settledResponse.json() as { message_id: string };
  // Keep the same page mounted: normal polling must remove the settled member without navigation.
  await expect(listCard.getByText(lease.question, { exact: true })).toBeHidden({ timeout: 25_000 });
  await walk.state("02b-mounted-needs-you-clears-settled-member", {
    visible: [listCard.getByText(policy.question, { exact: true })],
    hidden: [listCard.getByText(lease.question, { exact: true })],
  });
  const current = await readTask(request, slug);
  const resolved = current.questions.find((row) => row.id === lease.id && row.revision === lease.revision)!;
  expect(resolved.audience).toBe("operator");
  expect(resolved.resolution).toMatchObject({ by: "l3", recorded_by: "l2", recorded_attempt: 1, message_id: settled.message_id });
  expect(resolved.resolution?.l3_authority).toContain("Recorded task lease includes tests/");
  expect(current.messages.find((row) => row.id === settled.message_id)?.role).toBe("l3");
  expect(current.question_group.questions.find((row) => row.id === policy.id)).toMatchObject({ status: "open", audience: "operator", revision: policy.revision });
  expect(current.state).toBe(initial.state);
  expect(current.state).toBe("blocked");
  expect(current.session_id).toBe(initial.session_id);
  expect(current.hold_merge).toBe("Operator security review");
  await walk.open(atQuestion(slug, lease));
  const card = questionCard(page, lease);
  await recorded(card);
  await walk.state("03-task-receipt-retains-l3-attribution", {
    visible: [card.getByText(/^l3 ·/), questionCard(page, policy).getByText(policy.question, { exact: true })],
    hidden: [card.getByRole("button")],
  });
  await walk.open("/");
  await walk.state("04-needs-you-retains-only-operator-choice", {
    visible: [listCard.getByText(policy.question, { exact: true })],
    hidden: [listCard.getByText(lease.question, { exact: true })],
  });
  expect((await queue(request)).filter((row) => row.slug === slug)).toHaveLength(1);
  const project = await (await request.get("/api/project/atlas")).json();
  expect(project.decisions.filter((row: { slug: string }) => row.slug === slug).map((row: { id: string }) => row.id)).toEqual([policy.id]);
  const finalOverview = await (await request.get("/api/overview")).json();
  expect(finalOverview.projects[0].counts).toEqual(initialOverview.projects[0].counts);
  await walk.open(info.project.name === "phone" ? "/projects/atlas?tab=work" : "/projects/atlas");
  const work = page.getByRole("region", { name: "Work", exact: true });
  const projectRow = work.getByRole("link", { name: /^Lease and policy · Your turn · 1 question/ });
  await walk.state("05-project-retains-operator-choice-and-blocked-task", {
    visible: [projectRow],
    hidden: [work.getByRole("article"), work.getByText(policy.question, { exact: true }), work.getByText(lease.question, { exact: true })],
  });
});

test("Work rows retain running questions and partial answers, then keep the task after the final decision", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, initial } = await createGroup(request);
  const [retention, region, owner] = initial.question_group.questions as [Question, Question, Question];
  const workPath = "/projects/atlas?tab=work";
  const work = page.getByRole("region", { name: "Work", exact: true });
  const row = work.getByRole("link", { name: /^Rollout decisions ·/ });
  const list = page.getByRole("article", { name: "Rollout decisions", exact: true });
  const primary = page.getByRole("navigation", { name: info.project.name === "phone" ? "Primary" : "Rail", exact: true });
  const badge = (count: number) => primary.getByRole("link", { name: /Needs you/ }).locator(".badge").filter({ hasText: new RegExp(`^${count}$`) });
  const back = info.project.name === "phone" ? page.getByRole("button", { name: "Back", exact: true }) : page.locator(".task-crumb");
  await walk.open(workPath);
  await expect(row).toHaveCount(1);
  await walk.state("01-work-single-row-three-questions", {
    visible: [row.getByText(/Your turn · 3 questions/), badge(5)],
    hidden: [work.getByRole("article"), work.getByText(retention.question, { exact: true })],
  });
  await expect(primary.locator(".badge")).toHaveCount(1);
  await row.click();
  await expect(page).toHaveURL(new RegExp(`question=${retention.id}&revision=${retention.revision}$`));
  await expect(groupCard(page, initial.question_group)).toBeInViewport();
  await walk.state("02-work-opens-owning-question", {
    visible: [questionCard(page, retention), back], hidden: [work, page.getByRole("region", { name: "Live session", exact: true })],
  });
  await send(page, "Could we roll back after day seven?");
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  await modelCheckpoint(request, slug);
  await back.click();
  await expect(page).toHaveURL(workPath);
  await walk.state("03-running-followup-hands-turn-back", {
    visible: [row.getByText("L2 replying to you", { exact: true }), badge(2)], hidden: [row.getByText(/Your turn/), work.getByRole("article")],
  });
  await page.goForward();
  await handedBack(page, questionCard(page, retention));
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(page.getByRole("group", { name: "Stop this task?", exact: true })).toBeHidden();
  await expect(page.getByRole("button", { name: "Continue session", exact: true })).toBeVisible();
  await expect.poll(async () => (await readTask(request, slug)).steering.state).toBe("stopped");
  expect((await readTask(request, slug)).question_group.questions.filter((q) => q.status === "open")).toHaveLength(3);
  await back.click();
  await expect(page).toHaveURL(workPath);
  // Stop is the operator's own action: the turn stays with the L2 and the questions stay open.
  await walk.state("03b-stopped-task-retains-questions-and-danger-color", {
    visible: [row.getByText("Stopped by you", { exact: true }), badge(2)], hidden: [row.getByText(/Your turn/), work.getByRole("article")],
  });
  await expect(row.locator(".dot")).toHaveAttribute("data-state", "danger");
  await row.click();
  await page.getByRole("region", { name: "Task conversation", exact: true }).getByRole("button", { name: "Continue session", exact: true }).click();
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  await park(request, slug);
  await back.click();
  await walk.state("03c-owner-asks-all-three-again", {
    visible: [row.getByText(/Your turn · 3 questions/), row.getByText("Waiting for you", { exact: true }), badge(5)], hidden: [work.getByRole("article")],
  });
  await primary.getByRole("link", { name: /Needs you/ }).click();
  await list.getByRole("button", { name: "14 days", exact: true }).click();
  await list.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect(list).toBeHidden();
  await walk.state("04-inbox-partial-answer-hands-turn-back", {
    visible: [page.getByRole("heading", { name: "Needs you", exact: true }), badge(2)],
    hidden: [list],
  });
  expect((await readTask(request, slug)).question_group.questions.filter((q) => q.status === "open" && !q.response)).toHaveLength(2);
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  await walk.open(workPath);
  await walk.state("05-row-replying-after-answer", {
    visible: [row.getByText("L2 replying to you", { exact: true }), badge(2)], hidden: [row.getByText(/Your turn/), work.getByRole("article")],
  });
  await modelCheckpoint(request, slug);
  const again = await park(request, slug);
  expect(again.questions.filter((q) => q.status === "open").map((q) => q.id)).toEqual([region.id, owner.id]);
  await walk.state("05b-owner-asks-remaining-two-again", {
    visible: [row.getByText(/Your turn · 2 questions/), row.getByText("Waiting for you", { exact: true }), badge(4)], hidden: [work.getByRole("article")],
  });
  await primary.getByRole("link", { name: /Needs you/ }).click();
  await list.getByRole("button", { name: "West", exact: true }).click();
  await submit(page, request, slug);
  await expect(list).toBeHidden();
  await park(request, slug);
  await walk.open(workPath);
  await walk.state("06-same-row-last-question", {
    visible: [row.getByText(/Your turn · 1 question/), row.getByText("Waiting for you", { exact: true }), badge(3)], hidden: [work.getByRole("article")],
  });
  await row.click();
  await expect(page).toHaveURL(new RegExp(`question=${owner.id}&revision=${owner.revision}$`));
  await send(page, "Release team");
  await modelCheckpoint(request, slug);
  expect((await readTask(request, slug)).question_group.questions.every((q) => q.status === "resolved")).toBe(true);
  await back.click();
  await expect(page).toHaveURL(workPath);
  await walk.state("07-answered-task-still-current", {
    visible: [row.getByText("L2 replying to you", { exact: true }), badge(2)], hidden: [row.getByText(/Your turn/), work.getByRole("article")],
  });
  await expect(row).toHaveCount(1);
  await expect(row).toHaveAttribute("href", taskPath(slug));
});

test("a concise decision retains its material consequence and reveals complete technical context in chat", async ({ page, request }, info) => {
  const response = await request.post("/fixture/long-context");
  expect(response.ok()).toBe(true);
  const { slug, detail } = await response.json() as { slug: string; detail: string };
  const initial = await readTask(request, slug);
  const question = initial.question!;
  const walk = walkthrough(page, info);
  const list = page.getByRole("article", { name: "Choose retention window", exact: true });
  const evidence = "Final verification: cleanup waits for the last retained backup and records its result in the task conversation.";
  await walk.open("/");
  await list.scrollIntoViewIfNeeded();
  await expect(list.getByText("Longer instant rollback uses twice the temporary storage.", { exact: true })).toBeInViewport();
  await expect(list.getByRole("button", { name: "Keep 14 days", exact: true })).toBeInViewport();
  await walk.state("01-concise-decision-and-material-consequence", {
    visible: [list.getByText("Choose retention window", { exact: true }), list.getByText(question.question, { exact: true }), list.getByText("Longer instant rollback uses twice the temporary storage.", { exact: true }), list.getByRole("button", { name: "Keep 14 days", exact: true })],
    hidden: [list.getByText(evidence, { exact: false }), list.getByText("More context", { exact: true })],
  });
  await list.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  const card = questionCard(page, question);
  const context = card.locator("details").filter({ has: page.getByText("More context", { exact: true }) });
  await walk.state("02-owning-chat-context-collapsed", {
    visible: [card.getByText(question.question, { exact: true }), context.locator("summary")],
    hidden: [card.getByText(evidence, { exact: false })],
  });
  await walk.state("03-complete-context-expanded", {
    action: () => context.locator("summary").click(), visible: [context.getByText(evidence, { exact: false })], hidden: [],
  });
  for (const paragraph of detail.split("\n\n")) await expect(context.getByText(paragraph, { exact: true })).toBeVisible();
  await walk.state("04-context-collapsed-again", {
    action: () => context.locator("summary").click(), visible: [card.getByRole("button", { name: "Keep 14 days", exact: true })],
    hidden: [context.getByText(evidence, { exact: false })],
  });
  expect((await readTask(request, slug)).question?.status).toBe("open");
});

test("one global inbox spans projects while Work and question context retain their owner", async ({ page, request }, info) => {
  const created = await request.post("/fixture/second-project");
  expect(created.ok()).toBe(true);
  const { slug } = await created.json() as { slug: string };
  const walk = walkthrough(page, info);
  const work = page.getByRole("region", { name: "Work", exact: true });
  const primary = page.getByRole("navigation", { name: info.project.name === "phone" ? "Primary" : "Rail", exact: true });
  await walk.open("/projects/atlas?tab=work");
  await walk.state("01-work-is-selected-project-only", {
    visible: [work.getByRole("link", { name: /^Index rollout · Your turn · 1 question/ })],
    hidden: [work.getByRole("link", { name: /^Choose backup retention/ })],
  });
  await expect(page.locator(".badge:visible")).toHaveCount(1);
  if (info.project.name === "phone") {
    await page.getByRole("button", { name: "atlas", exact: true }).click();
    const switcher = page.getByRole("dialog", { name: "Switch project", exact: true });
    await walk.state("01b-project-switcher-without-competing-counts", {
      visible: [switcher.getByRole("link", { name: "atlas", exact: true }), switcher.getByRole("link", { name: "beacon", exact: true })],
      hidden: [switcher.locator(".badge")],
    });
    await page.keyboard.press("Escape");
  }
  await primary.getByRole("link", { name: /Needs you/ }).click();
  const card = page.getByRole("article", { name: "Choose backup retention", exact: true });
  await walk.state("02-cross-project-inbox", {
    visible: [page.getByText(/^3 questions across 2 projects/), card, page.getByRole("article", { name: "Index rollout", exact: true })],
    hidden: [work],
  });
  await expect(page.locator(".badge:visible")).toHaveCount(1);
  await card.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/beacon/tasks/${slug}\\?question=`));
  await walk.state("03-owning-project-chat", {
    visible: [page.getByRole("region", { name: "Task conversation", exact: true }), page.getByRole("textbox", { name: "Message the L2", exact: true })],
    hidden: [work],
  });
  const back = info.project.name === "phone" ? page.getByRole("button", { name: "Back", exact: true }) : page.locator(".task-crumb");
  await back.click();
  await expect(page).toHaveURL("/");
  await walk.state("04-back-to-global-inbox", { visible: [card], hidden: [page.getByRole("region", { name: "Task conversation", exact: true })] });
  await walk.open("/projects/beacon?tab=work");
  await walk.state("05-other-project-work", {
    visible: [work.getByRole("link", { name: /^Choose backup retention · Your turn · 1 question/ })],
    hidden: [work.getByRole("link", { name: /^Index rollout/ })],
  });
});

test("the L3 dilemma ends the chat; a follow-up hands the turn back until it is asked again, a simple answer resolves in the same L2", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "index-rollout";
  const initial = await readTask(request, slug);
  const question = initial.question!;
  const listCard = page.getByRole("article", { name: "Index rollout", exact: true });
  await walk.open("/");
  await walk.state("01-needs-you-recommendation", {
    visible: [listCard, listCard.getByRole("button", { name: question.recommendation!.label, exact: true })],
    hidden: [listCard.getByLabel("Follow-ups")],
  });
  await listCard.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${taskPath(slug)}\\?question=${question.id}&revision=${question.revision}$`));
  const card = questionCard(page, question);
  await expect(card).toBeInViewport();
  await expect(card.locator("..").locator("..")).toBeFocused();
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  // The open question follows every later message instead of sitting at its original anchor.
  await expect(conversation.locator(".msg-row").filter({ hasText: "Read-only verification 9: fixture migration remains healthy." }).locator("~ .conversation-question")).toHaveCount(1);
  await expect(conversation.getByText("The new index passes the fixture checks. Keeping the old one preserves instant rollback.", { exact: true })).toHaveCount(1);
  await walk.state("02-question-at-the-end-with-l3-context", {
    visible: [turnLabel(page).getByText("Your turn · 1 question", { exact: true }), card, card.getByText(question.question, { exact: true }), ...(info.project.name === "phone" ? [] : [page.getByText("Replying hands the turn back to the L2.", { exact: true })]), card.getByRole("button", { name: question.recommendation!.label, exact: true }), page.getByRole("textbox", { name: "Message the L2", exact: true })],
    hidden: [page.getByRole("combobox", { name: "Recipient", exact: true }), page.getByPlaceholder("Add a note for the L2 (optional)")],
  });
  await expect(conversation.getByText("The brief sets no retention limit; the operator should choose the rollback window.", { exact: true })).toHaveCount(1);
  await send(page, "Could we roll back after day seven?");
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  expect((await readTask(request, slug)).question?.status).toBe("open");
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(false);
  const reply = await modelCheckpoint(request, slug);
  // Let the real pending-question poll deliver the owner reply above the quiet line.
  await expect(conversation.getByText(reply.answer, { exact: true })).toHaveCount(1, { timeout: 25_000 });
  await handedBack(page, card);
  await walk.state("03-follow-up-hands-turn-back", {
    visible: [conversation.getByText("Could we roll back after day seven?", { exact: true }), conversation.getByText(reply.answer, { exact: true })],
    hidden: [card, turnLabel(page), page.getByText("Replying hands the turn back to the L2.", { exact: true })],
  });
  await park(request, slug);
  await expect(card).toBeVisible({ timeout: 25_000 });
  await walk.state("03b-owner-asks-again", {
    visible: [turnLabel(page).getByText("Your turn · 1 question · asked again", { exact: true }), card.getByRole("button", { name: question.recommendation!.label, exact: true }), conversation.getByText(reply.answer, { exact: true })],
    hidden: [card.getByText("Decision recorded", { exact: true }), conversation.getByRole("status").filter({ hasText: "Sent · the L2 has your reply." })],
  });
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(true);
  await send(page, "Maybe two weeks, but I am unsure about cost.");
  await modelCheckpoint(request, slug);
  expect((await readTask(request, slug)).question?.status).toBe("open");
  await send(page, "14 days");
  // Sending alone cannot be mistaken for semantic authorization.
  expect((await readTask(request, slug)).question?.status).toBe("open");
  const chosen = await modelCheckpoint(request, slug);
  const final = await readTask(request, slug);
  const resolved = final.questions.find((row) => row.id === question.id && row.revision === question.revision)!;
  expect(resolved.resolution).toMatchObject({ disposition: "answered", message_id: chosen.message_id, text: "Keep the old index for fourteen days." });
  expect(final.session_id).toBe(initial.session_id);
  const workers = await (await request.get("/fixture/workers")).json();
  // The first follow-up and the reply after the owner parked again each resumed the same session.
  expect(workers.calls).toHaveLength(2);
  for (const call of workers.calls) expect(call.session_id).toBe(initial.session_id);
  expect(workers.calls[0].prompt).toContain(question.id);
  await page.reload();
  await recorded(card);
  await walk.state("04-simple-decision-recorded-work-resumed", {
    visible: [card, conversation.getByText("Work resumed", { exact: true }), page.getByRole("textbox", { name: "Message the L2", exact: true })],
    hidden: [card.getByRole("button")],
  });
  await page.goBack();
  await walk.state("05-back-to-needs-you-cleared", { visible: [page.getByRole("heading", { name: "Needs you", exact: true })], hidden: [listCard] });
  await page.goForward();
  await recorded(card);
  const activity = conversation.locator("details").filter({ has: page.getByText("Activity & evidence", { exact: true }) });
  await activity.locator("summary").click();
  await walk.state("06-technical-activity-on-demand", { visible: [activity.getByRole("link", { name: "Open live session", exact: true })], hidden: [] });
  await activity.locator("summary").click();
  await expect(activity.getByRole("link", { name: "Open live session", exact: true })).toBeHidden();
});

test("partial answers retain only the relevant remainder; a changed direction closes the obsolete dilemma", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "retention-and-backup";
  const original = (await readTask(request, slug)).question!;
  await walk.open(atQuestion(slug, original));
  const oldCard = questionCard(page, original);
  await walk.state("01-related-questions-no-invented-acceptance", { visible: [oldCard], hidden: [oldCard.getByRole("button")] });
  await send(page, "Keep it for fourteen days. I still need to choose the backup region.");
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  await modelCheckpoint(request, slug);
  const remaining = (await readTask(request, slug)).question!;
  expect(remaining.question).toBe("Which backup region should we use?");
  expect(remaining.id).toBe(original.id);
  expect(remaining.revision).toBe(original.revision + 1);
  await page.reload();
  await recorded(oldCard);
  const currentCard = questionCard(page, remaining);
  await currentCard.scrollIntoViewIfNeeded();
  await walk.state("02-only-unanswered-remainder-open", { visible: [currentCard, currentCard.getByText(remaining.question, { exact: true })], hidden: [currentCard.getByRole("button", { name: "Other…", exact: true })] });
  await send(page, "Use the west region.");
  expect((await readTask(request, slug)).messages.find((row) => row.text === "Use the west region.")?.question_refs)
    .toEqual([{ id: remaining.id, revision: remaining.revision }]);
  await modelCheckpoint(request, slug);
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(false);
  await page.reload();
  await recorded(currentCard);
  await walk.state("03-both-parts-resolved", { visible: [currentCard], hidden: [currentCard.getByRole("button")] });

  const otherSlug = "index-rollout";
  const obsolete = (await readTask(request, otherSlug)).question!;
  await walk.open(atQuestion(otherSlug, obsolete));
  await send(page, "Use snapshots instead; the old index is no longer needed.");
  await expect.poll(async () => (await readTask(request, otherSlug)).state).toBe("running");
  await modelCheckpoint(request, otherSlug);
  expect((await queue(request)).some((row) => row.slug === otherSlug)).toBe(false);
  await page.reload();
  const closed = questionCard(page, obsolete);
  await recorded(closed, "superseded");
  await walk.state("04-changed-direction-closes-old-question", {
    visible: [closed.getByText("Use snapshots instead; old-index retention is no longer relevant.", { exact: true })],
    hidden: [closed.getByText("Decision recorded", { exact: true }), closed.getByRole("button")],
  });
});

test("a submitted answer survives a lost response without duplicate messages, and stale navigation shows the hand-back", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "index-rollout";
  const initial = await readTask(request, slug);
  const question = initial.question!;
  let payload: unknown;
  await walk.open("/");
  const card = page.getByRole("article", { name: "Index rollout", exact: true });
  // The real write completes; only its first HTTP response is lost at the browser boundary.
  await page.route("**/api/decide", async (route) => {
    payload = route.request().postDataJSON();
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    await route.abort("failed");
  }, { times: 1 });
  await card.getByRole("button", { name: question.recommendation!.label, exact: true }).click();
  await page.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect.poll(async () => (await readTask(request, slug)).questions.find((row) => row.id === question.id && row.revision === question.revision)?.response?.text).toBe(question.recommendation!.text);
  const retry = await request.post("/api/decide", { data: payload });
  expect(retry.ok()).toBe(true);
  const after = await readTask(request, slug);
  expect(after.questions.find((row) => row.id === question.id && row.revision === question.revision)!.resolution).toBeNull();
  const receipt = after.questions.find((row) => row.id === question.id && row.revision === question.revision)!.response!;
  expect(after.messages.filter((row) => row.id === receipt.message_id)).toHaveLength(1);
  expect(after.messages.find((row) => row.id === receipt.message_id)?.text).toContain(question.recommendation!.text);
  await page.reload();
  await walk.state("01-acceptance-clears-list-on-refresh", { visible: [page.getByRole("heading", { name: "Needs you", exact: true })], hidden: [card] });
  await walk.open(atQuestion(slug, question));
  const historical = questionCard(page, question);
  const answer = page.getByRole("region", { name: "Task conversation", exact: true }).locator(".bubble").filter({ hasText: question.recommendation!.text });
  await handedBack(page, historical);
  await walk.state("02-stale-link-shows-sent-answer-no-old-action", { visible: [answer], hidden: [historical, page.getByRole("button", { name: question.recommendation!.label, exact: true })] });
  await walk.open(`/projects/atlas/decisions/${slug}`);
  await expect(page).toHaveURL(new RegExp(`/projects/atlas/tasks/${slug}(\\?|$)`));
  await handedBack(page, historical);
  await walk.state("03-legacy-link-opens-owning-chat", { visible: [answer, page.getByRole("textbox", { name: "Message the L2", exact: true })], hidden: [page.getByRole("combobox", { name: "Recipient", exact: true })] });
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  expect((await readTask(request, slug)).session_id).toBe(initial.session_id);
  expect((await (await request.get("/fixture/workers")).json()).calls).toHaveLength(1);
});

test("a revised recommendation refuses the stale action and anchors the current question", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "index-rollout";
  const old = (await readTask(request, slug)).question!;
  await walk.open(atQuestion(slug, old));
  const oldCard = questionCard(page, old);
  await expect(oldCard.getByRole("button", { name: old.recommendation!.label, exact: true })).toBeEnabled();
  expect((await request.post("/fixture/revise", { data: { slug } })).ok()).toBe(true);
  const current = (await readTask(request, slug)).question!;
  expect(current.revision).toBe(old.revision + 1);
  const stale = await request.post("/api/decide", { data: { project: "atlas", slug, question_id: old.id, revision: old.revision } });
  expect(stale.status()).toBe(409);
  expect((await readTask(request, slug)).question?.status).toBe("open");
  await page.reload();
  await recorded(oldCard, "superseded");
  await walk.state("01-stale-revision-readable-without-action", { visible: [oldCard], hidden: [oldCard.getByRole("button")] });
  await walk.open(atQuestion(slug, current));
  const currentCard = questionCard(page, current);
  await expect(currentCard).toBeInViewport();
  await walk.state("02-current-recommendation-only", { visible: [currentCard.getByRole("button", { name: "Use 14 days & resume", exact: true })], hidden: [oldCard.getByRole("button")] });
  await currentCard.getByRole("button", { name: "Use 14 days & resume", exact: true }).click();
  await submit(page, request, slug);
  await recorded(currentCard);
  await walk.state("03-current-acceptance-recorded", { visible: [currentCard], hidden: [currentCard.getByRole("button")] });
});

test("a requeued dilemma accepts a durable response and discussion while its next worker waits to start", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "index-rollout";
  const initial = await readTask(request, slug);
  const question = initial.question!;
  const requeued = await request.post("/fixture/requeue", { data: { slug } });
  expect(requeued.ok()).toBe(true);
  const waiting = await readTask(request, slug);
  expect(waiting.state).toBe("queued");
  expect(waiting.agent_id).toBeNull();
  expect(waiting.question).toMatchObject({ id: question.id, revision: question.revision, status: "open" });
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(true);
  await walk.open("/projects/atlas?tab=work");
  await walk.state("00-requeued-question-discoverable-in-work", {
    visible: [page.getByRole("region", { name: "Work", exact: true }).getByRole("link", { name: /^Index rollout · Your turn · 1 question · Queued/ })],
    hidden: [page.getByRole("region", { name: "Work", exact: true }).getByRole("article")],
  });
  await walk.open(atQuestion(slug, question));
  const card = questionCard(page, question);
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = conversation.getByRole("textbox", { name: "Message the L2", exact: true });
  const accept = card.getByRole("button", { name: question.recommendation!.label, exact: true });
  await expect(field).toBeEnabled();
  await expect(accept).toBeEnabled();
  await walk.state("01-requeued-question-still-actionable", {
    visible: [card, field, accept, page.getByText("Your turn · 1 question", { exact: true }).first(), ...(info.project.name === "phone" ? [] : [conversation.getByText("Delivered when Altitude starts the L2.", { exact: true })])],
    hidden: [page.getByRole("button", { name: "Resume", exact: true }), ...(info.project.name === "phone" ? [conversation.getByText("Delivered when Altitude starts the L2.", { exact: true })] : [])],
  });
  await accept.click();
  await submit(page, request, slug, false);
  await handedBack(page, card, "Sent · waiting for the L2 to start.");
  const decided = await readTask(request, slug);
  expect(decided.state).toBe("queued");
  const receipt = decided.questions.find((row) => row.id === question.id && row.revision === question.revision)!.response!;
  expect(decided.questions.find((row) => row.id === question.id && row.revision === question.revision)!.resolution).toBeNull();
  expect(decided.messages.filter((row) => row.id === receipt.message_id)).toHaveLength(1);
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(false);
  let engine = await (await request.get("/fixture/workers")).json();
  expect(engine.calls).toHaveLength(0);
  await expect(field).toBeEnabled();
  await walk.state("02-answer-saved-worker-still-queued", {
    visible: [field, conversation.getByText("Sent · waiting for the L2 to start.", { exact: true })],
    hidden: [accept, conversation.getByText("Work resumed", { exact: true })],
  });
  const followup = "Could we roll back after day seven?";
  await send(page, followup);
  await expect(field).toHaveValue("");
  const discussed = await readTask(request, slug);
  expect(discussed.state).toBe("queued");
  expect(discussed.question?.status).toBe("open");
  expect(discussed.messages.filter((row) => row.text === followup)).toHaveLength(1);
  engine = await (await request.get("/fixture/workers")).json();
  expect(engine.calls).toHaveLength(0);
  expect(engine.pending[slug].filter((row: { id: string }) => row.id === receipt.message_id)).toHaveLength(1);
  expect(engine.pending[slug].filter((row: { text: string }) => row.text === followup)).toHaveLength(1);
  await walk.state("03-follow-up-saved-awaiting-worker", {
    visible: [conversation.locator(".bubble").filter({ hasText: followup }), field, conversation.getByText("Sent · waiting for the L2 to start.", { exact: true })],
    hidden: [accept, card.getByText("Decision recorded", { exact: true })],
  });
  await page.reload();
  await handedBack(page, card, "Sent · waiting for the L2 to start.");
  await walk.state("04-queued-hand-back-and-composer-survive-reload", { visible: [field, conversation.locator(".bubble").filter({ hasText: followup })], hidden: [accept] });
  await walk.open("/projects/atlas?tab=work");
  const work = page.getByRole("region", { name: "Work", exact: true });
  await walk.state("05-answered-task-remains-queued-in-work", {
    visible: [work.getByRole("link", { name: /^Index rollout · Queued/ })],
    hidden: [work.getByText(/Your turn ·/).filter({ hasText: "Index rollout" }), work.getByRole("article")],
  });
});

test("one question stages an explicit alternative before sending it to its owner", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "index-rollout";
  const initial = await readTask(request, slug);
  const question = initial.question!;
  await walk.open(atQuestion(slug, question));
  const card = questionCard(page, question);
  await expect(card.getByRole("button")).toHaveCount(4);
  await expect(card.getByRole("button", { name: "14 days", exact: true })).toHaveAttribute("aria-pressed", "false");
  await walk.state("review-02-single", {
    visible: [card.getByRole("button", { name: "7 days", exact: true }), card.getByRole("button", { name: "14 days", exact: true }), card.getByRole("button", { name: "30 days", exact: true })],
    hidden: [page.getByRole("button", { name: /^Send \d+ answers?$/ })],
  });
  const submitted = page.waitForRequest((row) => row.url().endsWith("/api/decide"));
  await card.getByRole("button", { name: "14 days", exact: true }).click();
  await submit(page, request, slug);
  expect((await submitted).postDataJSON()).toMatchObject({ answers: [{ question_id: question.id, revision: question.revision, option_key: "fourteen" }] });
  await recorded(card);
  const after = await readTask(request, slug);
  const resolution = after.questions.find((row) => row.id === question.id && row.revision === question.revision)!.resolution!;
  expect(resolution).toMatchObject({ text: "Keep the old index for fourteen days." });
  expect(after.messages.filter((row) => row.id === resolution.message_id)).toHaveLength(1);
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(false);
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  expect((await readTask(request, slug)).session_id).toBe(initial.session_id);
  expect((await (await request.get("/fixture/workers")).json()).calls).toHaveLength(1);
  await walk.state("alternative-interpreted-by-owner", { visible: [card.getByText(resolution.text, { exact: true })], hidden: [card.getByRole("button")] });
});

test("grouped choices start unselected, submit only picked answers, and the recommended choice is marked, not preselected", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const { slug, initial } = await createGroup(request);
  const group = initial.question_group;
  const [retention, region, owner] = group.questions as [Question, Question, Question];
  expect(group.questions).toHaveLength(3);
  await walk.open("/");
  const list = page.getByRole("article", { name: "Rollout decisions", exact: true });
  await expect(list).toHaveCount(1);
  for (const q of group.questions) await expect(list.getByText(q.question, { exact: true })).toBeVisible();
  await expect(list.locator('[aria-pressed="true"]')).toHaveCount(0);
  await list.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  const card = groupCard(page, group);
  await expect(card.locator("..")).toBeFocused();
  await expect(card.locator('[aria-pressed="true"]')).toHaveCount(0);
  const west = questionCard(page, region).getByRole("button", { name: "West", exact: true });
  await expect(west).toHaveAttribute("aria-description", "Recommended");
  await expect(west.getByText("Recommended", { exact: true })).toBeVisible();
  await expect(west).toHaveAttribute("aria-pressed", "false");
  await walk.state("review-02b-group-recommended-marked", { visible: [west], hidden: [card.locator('[aria-pressed="true"]')] });
  const submissions: unknown[] = [];
  page.on("request", (row) => { if (row.url().endsWith("/api/decide")) submissions.push(row.postDataJSON()); });
  await questionCard(page, retention).getByRole("button", { name: "14 days", exact: true }).click();
  await questionCard(page, region).getByRole("button", { name: "East", exact: true }).click();
  await expect(card.getByRole("button", { name: "Send 2 answers", exact: true })).toBeVisible();
  expect(submissions).toHaveLength(0);
  expect((await readTask(request, slug)).question_group.questions.every((q) => q.status === "open")).toBe(true);
  await card.locator("..").evaluate((node) => node.scrollIntoView({ block: "start" }));
  await expect(questionCard(page, retention).getByText(retention.question, { exact: true })).toBeInViewport();
  await walk.state("review-03-group", {
    visible: [card.getByText(owner.question, { exact: true }), card.getByRole("button", { name: "Send 2 answers", exact: true })],
    hidden: [card.locator('[data-recommended][aria-pressed="true"]')],
  });
  // Deselecting the region leaves an explicit one-answer batch, never a default for the other questions.
  await questionCard(page, region).getByRole("button", { name: "East", exact: true }).click();
  await submit(page, request, slug);
  await handedBack(page, questionCard(page, retention));
  await park(request, slug);
  await recorded(questionCard(page, retention));
  await expect(turnLabel(page)).toHaveText("Your turn · 2 questions · asked again");
  expect(submissions).toEqual([{ project: "atlas", slug, group_id: group.id, group_revision: group.revision,
    answers: [{ question_id: retention.id, revision: retention.revision, option_key: "fourteen" }] }]);
  let after = await readTask(request, slug);
  expect(after.question_group.questions.filter((q) => q.status === "open").map((q) => q.id)).toEqual([region.id, owner.id]);
  expect(after.question_group.questions[0]!.response?.text).toBe("Keep the old index for fourteen days.");
  await west.click();
  await expect(west).toHaveAttribute("aria-pressed", "true");
  await submit(page, request, slug);
  await handedBack(page, questionCard(page, region));
  await park(request, slug);
  await recorded(questionCard(page, region));
  after = await readTask(request, slug);
  expect(after.question_group.questions.filter((q) => q.status === "open").map((q) => q.id)).toEqual([owner.id]);
  expect(after.question_group.questions[0]!.response?.text).toBe("Keep the old index for fourteen days.");
  expect(after.question_group.questions[1]!.response?.text).toBe("Use the west region for backups.");
  await questionCard(page, owner).evaluate((node) => node.scrollIntoView({ block: "center" }));
  await expect(card.getByText(owner.question, { exact: true })).toBeInViewport();
  await walk.state("review-05-partial", { visible: [card.getByText(owner.question, { exact: true }), page.getByRole("textbox", { name: "Message the L2", exact: true })], hidden: [west] });
  await page.goBack();
  await expect(list.getByText(owner.question, { exact: true })).toBeVisible();
  await expect(list.getByText(retention.question, { exact: true })).toHaveCount(0);
  await expect(list.getByText(region.question, { exact: true })).toHaveCount(0);
  await list.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  await send(page, "Release team");
  expect((await readTask(request, slug)).question_group.questions.filter((q) => q.status === "open")).toHaveLength(1);
  const response = await modelCheckpoint(request, slug);
  after = await readTask(request, slug);
  expect(after.question_group.questions[2]!.resolution?.message_id).toBe(response.message_id);
  expect(after.question_group.questions.every((q) => q.status === "resolved")).toBe(true);
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(false);
  expect(after.session_id).toBe(initial.session_id);
  // Each answer after the owner parked again resumed the same session.
  const calls = (await (await request.get("/fixture/workers")).json()).calls as { session_id: string }[];
  expect(calls).toHaveLength(3);
  for (const call of calls) expect(call.session_id).toBe(initial.session_id);
  await page.reload();
  for (const q of group.questions) await recorded(questionCard(page, q));
  await page.getByRole("button", { name: "Latest messages", exact: true }).click();
  const resumed = conversation.getByText("Work resumed", { exact: true });
  await expect(resumed).toBeInViewport();
  await expect(conversation.locator(".bubble").getByText("Release team", { exact: true })).toBeInViewport();
  await walk.state("review-06-accepted", { visible: [resumed, conversation.getByText("The answers are recorded. I will continue with the rollout.", { exact: true })], hidden: [card.getByRole("button")] });
});

test("a group follow-up accepts nothing; one typed decision answers several members and closes an irrelevant question", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, initial } = await createGroup(request);
  const group = initial.question_group;
  const [retention, region, owner] = group.questions as [Question, Question, Question];
  await walk.open(atQuestion(slug, retention));
  await send(page, "Could we roll back after day seven?");
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  const reply = await modelCheckpoint(request, slug);
  let after = await readTask(request, slug);
  expect(after.question_group.questions.every((q) => q.status === "open")).toBe(true);
  const source = after.messages.find((m) => m.id === reply.message_id)!;
  expect(source.question_refs).toEqual(group.questions.map((q) => ({ id: q.id, revision: q.revision })));
  await page.reload();
  const card = groupCard(page, group);
  await handedBack(page, card);
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(false);
  await park(request, slug);
  await page.reload();
  await walk.state("group-follow-up-asked-again-keeps-all-three-open", { visible: [turnLabel(page).getByText("Your turn · 3 questions · asked again", { exact: true }), card.getByText("3 questions to answer", { exact: true })], hidden: [card.getByText("Decision recorded", { exact: true })] });
  await send(page, "Keep 14 days; use snapshots so the backup region no longer matters.");
  expect((await readTask(request, slug)).question_group.questions.every((q) => q.status === "open")).toBe(true);
  const chosen = await modelCheckpoint(request, slug);
  after = await readTask(request, slug);
  expect(after.question_group.questions[0]!.resolution).toMatchObject({ disposition: "answered", message_id: chosen.message_id });
  expect(after.question_group.questions[1]!.resolution).toMatchObject({ disposition: "superseded", message_id: chosen.message_id });
  expect(after.question_group.questions[2]!.status).toBe("open");
  expect((await queue(request)).filter((row) => row.slug === slug)).toHaveLength(0);
  await park(request, slug);
  expect((await queue(request)).filter((row) => row.slug === slug)).toHaveLength(1);
  await page.reload();
  await recorded(questionCard(page, retention));
  await recorded(questionCard(page, region), "superseded");
  await walk.state("typed-decision-closes-answered-and-irrelevant-members", {
    visible: [card.getByText("Snapshots make the backup region unnecessary.", { exact: true }), card.getByText(owner.question, { exact: true })],
    hidden: [questionCard(page, retention).getByRole("button"), questionCard(page, region).getByRole("button")],
  });
  await send(page, "Release team");
  await modelCheckpoint(request, slug);
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(false);
  expect((await readTask(request, slug)).session_id).toBe(initial.session_id);
});

test("one typed sentence can resolve all three questions without selecting quick answers", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, initial } = await createGroup(request);
  const group = initial.question_group;
  await walk.open(atQuestion(slug, group.questions[0]!));
  await send(page, "Keep 14 days, use West, and send the report to the release team.");
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  expect((await readTask(request, slug)).question_group.questions.every((q) => q.status === "open")).toBe(true);
  const chosen = await modelCheckpoint(request, slug);
  const after = await readTask(request, slug);
  for (const q of after.question_group.questions) expect(q.resolution).toMatchObject({ disposition: "answered", message_id: chosen.message_id });
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(false);
  await page.reload();
  const card = groupCard(page, group);
  await walk.state("typed-answer-all-recorded", { visible: [card.getByText("Questions closed", { exact: true })], hidden: [card.getByRole("button")] });
});

test("a stale group rejects the whole batch, refresh retains unaffected picks, and retry stores one shared response", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, initial } = await createGroup(request);
  const group = initial.question_group;
  const [retention, region] = group.questions as [Question, Question, Question];
  await walk.open(atQuestion(slug, retention));
  const card = groupCard(page, group);
  await questionCard(page, retention).getByRole("button", { name: "14 days", exact: true }).click();
  await questionCard(page, region).getByRole("button", { name: "West", exact: true }).click();
  // Keep the observed revision until Send; background polling must not erase the stale-input case.
  const read = `**/api/task/atlas/${slug}`;
  const observed = await readTask(request, slug);
  await page.route(read, (route) => route.fulfill({ json: observed }));
  expect((await request.post("/fixture/revise-group", { data: { slug } })).ok()).toBe(true);
  const before = await readTask(request, slug);
  const staleResponse = page.waitForResponse((row) => row.url().endsWith("/api/decide"));
  await card.getByRole("button", { name: "Send 2 answers", exact: true }).click();
  expect((await staleResponse).status()).toBe(409);
  await page.unroute(read);
  const failed = await readTask(request, slug);
  expect(failed.messages).toEqual(before.messages);
  expect(failed.question_group.questions.every((q) => q.status === "open")).toBe(true);
  expect((await (await request.get("/fixture/workers")).json()).calls).toHaveLength(0);
  await walk.state("stale-batch-accepts-no-members", { visible: [card.getByRole("alert"), card.getByRole("button", { name: "Refresh", exact: true })], hidden: [card.getByText("Decision recorded", { exact: true })] });
  await page.unroute(read);
  await card.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(card.getByRole("alert")).toHaveCount(0);
  await expect(card.locator('[aria-pressed="true"]')).toHaveCount(1);
  await expect(questionCard(page, retention).getByRole("button", { name: "14 days", exact: true })).toHaveAttribute("aria-pressed", "true");
  const submitted = page.waitForRequest((row) => row.url().endsWith("/api/decide"));
  await card.getByRole("button", { name: "East", exact: true }).click();
  await submit(page, request, slug, false);
  const payload = (await submitted).postDataJSON();
  await handedBack(page, card);
  expect((await request.post("/api/decide", { data: payload })).ok()).toBe(true);
  const after = await readTask(request, slug);
  const receipts = after.question_group.questions.filter((q) => q.response).map((q) => q.response!);
  expect(receipts.map((row) => row.text)).toEqual(["Keep the old index for fourteen days.", "Use the east region for backups."]);
  expect(receipts[0]!.message_id).toBe(receipts[1]!.message_id);
  expect(after.messages.filter((m) => m.id === receipts[0]!.message_id)).toHaveLength(1);
  expect(after.question_group.questions.filter((q) => q.status === "open" && !q.response)).toHaveLength(1);
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  expect((await (await request.get("/fixture/workers")).json()).calls).toHaveLength(1);
  await walk.state("refreshed-answers-recorded-once-turn-handed-back", { visible: [page.getByText("Sent · the L2 has your reply.", { exact: true })], hidden: [card] });
});

test("question loading, read failure, write failure and denied access retain recoverable conversation state", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "index-rollout";
  const q = (await readTask(request, slug)).question!;
  const endpoint = `**/api/task/atlas/${slug}`;
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(endpoint, async (route) => { await gate; await route.continue(); }, { times: 1 });
  await walk.open(atQuestion(slug, q));
  try {
    await walk.state("01-question-loading", { visible: [page.getByLabel("Loading", { exact: true }).first()], hidden: [field] });
  } finally { release(); }
  const card = questionCard(page, q);
  await expect(card).toBeVisible();
  await page.route("**/api/l2/message", (route) => route.fulfill({ status: 503, json: { error: "Message could not be saved." } }), { times: 1 });
  await field.fill("Keep the draft after this failed send.");
  await page.getByRole("region", { name: "Task conversation", exact: true }).getByRole("button", { name: "Send", exact: true }).click();
  await expect(field).toHaveValue("Keep the draft after this failed send.");
  await walk.state("02-delivery-unconfirmed-draft-retained", {
    visible: [field, page.getByRole("alert").filter({ hasText: "Could not confirm delivery." })],
    hidden: [page.getByRole("button", { name: "Retry", exact: true }), page.getByRole("region", { name: "Task conversation", exact: true }).locator(".bubble").filter({ hasText: "Keep the draft after this failed send." })],
  });
  expect((await readTask(request, slug)).messages.some((row) => row.text === "Keep the draft after this failed send.")).toBe(false);
  await page.route("**/api/decide", (route) => route.fulfill({ status: 403, json: { error: "Write access denied." } }), { times: 1 });
  await card.getByRole("button", { name: q.recommendation!.label, exact: true }).click();
  await page.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect(card.getByRole("button", { name: q.recommendation!.label, exact: true })).toBeDisabled();
  await expect(field).toBeDisabled();
  await expect(field).toHaveValue("Keep the draft after this failed send.");
  await walk.state("03-denied-send-and-acceptance", {
    visible: [page.getByText("You cannot send messages or answers here.", { exact: false }), card],
    hidden: [card.getByText("Decision recorded", { exact: true })],
  });
  expect((await readTask(request, slug)).question?.status).toBe("open");
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(field).toBeEnabled();
  await expect(card.getByRole("button", { name: q.recommendation!.label, exact: true })).toBeEnabled();
  await walk.state("04-refresh-restores-writing", { visible: [field], hidden: [page.getByText("You cannot send messages or answers here.", { exact: false })] });
  await page.route(endpoint, (route) => route.fulfill({ status: 503, json: { error: "Question read unavailable." } }));
  await page.reload();
  const error = page.getByText(/Could not load the task/);
  await expect(error).toBeVisible({ timeout: 20_000 });
  await walk.state("05-initial-read-failed", { visible: [error, page.getByRole("button", { name: "Retry", exact: true }).first()], hidden: [field] });
  await page.unroute(endpoint);
  await page.getByRole("button", { name: "Retry", exact: true }).first().click();
  await walk.state("06-read-recovered", { visible: [card, field], hidden: [error] });
});
