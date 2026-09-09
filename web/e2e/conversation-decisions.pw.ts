import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "conversation-decisions-service.py" });
test.setTimeout(90_000);

type Question = { id: string; revision: number; anchor_id: string; status: string; question: string; options: { key: string; label: string; text: string }[]; recommended_key: string | null; recommendation: { text: string; label: string } | null; resolution: { disposition: string; message_id: string; text: string; option_key?: string } | null };
type Group = { id: string; revision: number; anchor_id: string; questions: Question[] };
type Task = { state: string; session_id: string; agent_id: string; question: Question | null; questions: Question[]; question_group: Group; messages: { id: string; text: string; role: string; question_refs?: { id: string; revision: number }[] }[] };
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
  await expect(conversation.locator(".bubble").filter({ hasText: text })).toBeVisible();
}
async function modelCheckpoint(request: APIRequestContext, slug: string) {
  const response = await request.post("/fixture/checkpoint", { data: { slug } });
  expect(response.ok()).toBe(true);
  return response.json() as Promise<{ message_id: string; answer: string }>;
}
async function recorded(card: Locator, disposition = "answered") {
  await expect(card).toHaveAttribute("data-status", "resolved");
  await expect(card.getByText(disposition === "answered" ? "Decision recorded" : "Question closed", { exact: true })).toBeVisible();
  await expect(card.getByRole("button")).toHaveCount(0);
}

// No route overlays: browser → real Handler/storage/wake → deterministic owner response.
test("Needs you anchors the L3 dilemma; follow-ups and ambiguity stay open, a simple answer resolves in the same L2", async ({ page, request }, info) => {
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
  await expect(conversation.getByText("Read-only verification 9: fixture migration remains healthy.", { exact: true })).not.toBeInViewport();
  await expect(conversation.getByText("The new index passes the fixture checks. Keeping the old one preserves instant rollback.", { exact: true })).toHaveCount(1);
  await walk.state("02-anchored-question-and-l3-context", {
    visible: [card, card.getByText(question.question, { exact: true }), card.getByRole("button", { name: question.recommendation!.label, exact: true }), page.getByRole("textbox", { name: "Message the L2", exact: true })],
    hidden: [page.getByRole("combobox", { name: "Recipient", exact: true }), page.getByPlaceholder("Add a note for the L2 (optional)")],
  });
  await expect(conversation.getByText("The brief sets no retention limit; the operator should choose the rollback window.", { exact: true })).toHaveCount(1);
  await send(page, "Could we roll back after day seven?");
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  expect((await readTask(request, slug)).question?.status).toBe("open");
  await conversation.getByRole("button", { name: "View question", exact: true }).click();
  const reply = await modelCheckpoint(request, slug);
  // Let the real pending-question poll deliver the owner reply while preserving the reading position.
  await expect(conversation.getByText(reply.answer, { exact: true })).toHaveCount(1, { timeout: 25_000 });
  await expect(card).toBeInViewport();
  await expect(conversation.getByRole("button", { name: "Latest messages", exact: true })).toBeVisible();
  await walk.state("03-follow-up-keeps-choice-open", {
    visible: [card, card.getByRole("button", { name: question.recommendation!.label, exact: true })],
    hidden: [card.getByText("Decision recorded", { exact: true })],
  });
  await walk.state("03b-follow-up-exchange", {
    action: () => conversation.getByRole("button", { name: "Latest messages", exact: true }).click(),
    visible: [conversation.getByText("Could we roll back after day seven?", { exact: true }), conversation.getByText(reply.answer, { exact: true }), conversation.getByRole("button", { name: "View question", exact: true })],
    hidden: [conversation.getByText("Decision recorded", { exact: true })],
  });
  await expect(conversation.getByText(reply.answer, { exact: true })).toBeInViewport();
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
  expect(workers.calls).toHaveLength(1);
  expect(workers.calls[0].session_id).toBe(initial.session_id);
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
  await walk.state("02-only-unanswered-remainder-open", { visible: [currentCard, currentCard.getByText(remaining.question, { exact: true })], hidden: [currentCard.getByRole("button")] });
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

test("quick acceptance survives a lost response without duplicate messages, and stale navigation shows the receipt", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "index-rollout";
  const initial = await readTask(request, slug);
  const question = initial.question!;
  const payload = { project: "atlas", slug, question_id: question.id, revision: question.revision };
  await walk.open("/");
  const card = page.getByRole("article", { name: "Index rollout", exact: true });
  // The real write completes; only its first HTTP response is lost at the browser boundary.
  await page.route("**/api/decide", async (route) => {
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    await route.abort("failed");
  }, { times: 1 });
  await card.getByRole("button", { name: question.recommendation!.label, exact: true }).click();
  await expect.poll(async () => (await readTask(request, slug)).questions.find((row) => row.id === question.id && row.revision === question.revision)?.status).toBe("resolved");
  const retry = await request.post("/api/decide", { data: payload });
  expect(retry.ok()).toBe(true);
  const after = await readTask(request, slug);
  const receipt = after.questions.find((row) => row.id === question.id && row.revision === question.revision)!.resolution!;
  expect(after.messages.filter((row) => row.id === receipt.message_id)).toHaveLength(1);
  expect(after.messages.find((row) => row.id === receipt.message_id)?.text).toContain(question.recommendation!.text);
  await page.reload();
  await walk.state("01-acceptance-clears-list-on-refresh", { visible: [page.getByRole("heading", { name: "Needs you", exact: true })], hidden: [card] });
  await walk.open(atQuestion(slug, question));
  const historical = questionCard(page, question);
  await recorded(historical);
  await walk.state("02-stale-link-keeps-receipt-no-old-action", { visible: [historical], hidden: [historical.getByRole("button")] });
  await walk.open(`/projects/atlas/decisions/${slug}`);
  await expect(page).toHaveURL(new RegExp(`/projects/atlas/tasks/${slug}(\\?|$)`));
  await recorded(historical);
  await walk.state("03-legacy-link-opens-owning-chat", { visible: [historical, page.getByRole("textbox", { name: "Message the L2", exact: true })], hidden: [page.getByRole("combobox", { name: "Recipient", exact: true })] });
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
  await recorded(currentCard);
  await walk.state("03-current-acceptance-recorded", { visible: [currentCard], hidden: [currentCard.getByRole("button")] });
});

test("a requeued dilemma accepts discussion and a durable decision while its next worker waits to start", async ({ page, request }, info) => {
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
  await walk.open(atQuestion(slug, question));
  const card = questionCard(page, question);
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = conversation.getByRole("textbox", { name: "Message the L2", exact: true });
  const accept = card.getByRole("button", { name: question.recommendation!.label, exact: true });
  await expect(field).toBeEnabled();
  await expect(accept).toBeEnabled();
  await walk.state("01-requeued-question-still-actionable", {
    visible: [card, field, accept, page.getByText("Queued", { exact: true }).first(), ...(info.project.name === "phone" ? [] : [conversation.getByText("Delivered when Altitude starts the L2.", { exact: true })])],
    hidden: [page.getByRole("button", { name: "Resume", exact: true }), ...(info.project.name === "phone" ? [conversation.getByText("Delivered when Altitude starts the L2.", { exact: true })] : [])],
  });
  const followup = "Could we roll back after day seven?";
  await send(page, followup);
  await expect(field).toHaveValue("");
  const discussed = await readTask(request, slug);
  expect(discussed.state).toBe("queued");
  expect(discussed.question?.status).toBe("open");
  expect(discussed.messages.filter((row) => row.text === followup)).toHaveLength(1);
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(true);
  let engine = await (await request.get("/fixture/workers")).json();
  expect(engine.calls).toHaveLength(0);
  expect(engine.pending[slug].filter((row: { text: string }) => row.text === followup)).toHaveLength(1);
  await walk.state("02-follow-up-saved-awaiting-worker", {
    visible: [conversation.locator(".bubble").filter({ hasText: followup }), field],
    hidden: [card.getByText("Decision recorded", { exact: true })],
  });
  await accept.click();
  await recorded(card);
  const decided = await readTask(request, slug);
  expect(decided.state).toBe("queued");
  const receipt = decided.questions.find((row) => row.id === question.id && row.revision === question.revision)!.resolution!;
  expect(receipt.disposition).toBe("answered");
  expect(decided.messages.filter((row) => row.id === receipt.message_id)).toHaveLength(1);
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(false);
  await expect(field).toBeEnabled();
  await walk.state("03-decision-recorded-worker-still-queued", {
    action: () => conversation.getByRole("button", { name: "Latest messages", exact: true }).click(),
    visible: [field, conversation.getByText("Waiting for the L2 to start", { exact: true })],
    hidden: [accept, conversation.getByText("Work resumed", { exact: true })],
  });
  const followOn = "Include the rollback window in the release notes.";
  await send(page, followOn);
  const final = await readTask(request, slug);
  expect(final.state).toBe("queued");
  expect(final.messages.filter((row) => row.text === followOn)).toHaveLength(1);
  engine = await (await request.get("/fixture/workers")).json();
  expect(engine.calls).toHaveLength(0);
  expect(engine.pending[slug].filter((row: { id: string }) => row.id === receipt.message_id)).toHaveLength(1);
  expect(engine.pending[slug].filter((row: { text: string }) => row.text === followOn)).toHaveLength(1);
  await page.reload();
  await recorded(card);
  await walk.state("04-queued-receipt-and-composer-survive-reload", { visible: [card, field], hidden: [accept] });
});

test("one question offers immediate explicit alternatives without a separate confirmation", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "index-rollout";
  const initial = await readTask(request, slug);
  const question = initial.question!;
  await walk.open(atQuestion(slug, question));
  const card = questionCard(page, question);
  await expect(card.getByRole("button")).toHaveCount(3);
  await expect(card.getByRole("button", { name: "14 days", exact: true })).not.toHaveAttribute("aria-pressed");
  await walk.state("review-02-single", {
    visible: [card.getByRole("button", { name: "7 days", exact: true }), card.getByRole("button", { name: "14 days", exact: true }), card.getByRole("button", { name: "30 days", exact: true })],
    hidden: [page.getByRole("button", { name: /^Send \d+ answers?$/ }), page.getByRole("button", { name: "Use recommendations", exact: true })],
  });
  const submitted = page.waitForRequest((row) => row.url().endsWith("/api/decide"));
  await card.getByRole("button", { name: "14 days", exact: true }).click();
  expect((await submitted).postDataJSON()).toMatchObject({ question_id: question.id, revision: question.revision, option_key: "fourteen" });
  await recorded(card);
  const after = await readTask(request, slug);
  const resolution = after.questions.find((row) => row.id === question.id && row.revision === question.revision)!.resolution!;
  expect(resolution).toMatchObject({ option_key: "fourteen", text: "Keep the old index for fourteen days." });
  expect(after.messages.filter((row) => row.id === resolution.message_id)).toHaveLength(1);
  expect((await queue(request)).some((row) => row.slug === slug)).toBe(false);
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  expect((await readTask(request, slug)).session_id).toBe(initial.session_id);
  expect((await (await request.get("/fixture/workers")).json()).calls).toHaveLength(1);
  await walk.state("alternative-recorded-immediately", { visible: [card.getByText(resolution.text, { exact: true })], hidden: [card.getByRole("button")] });
});

test("grouped choices start unselected, submit only picked answers, and recommendations preserve an earlier alternative", async ({ page, request }, info) => {
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
  await expect(card.getByRole("button", { name: "Use recommendations", exact: true })).toBeVisible();
  const submissions: unknown[] = [];
  page.on("request", (row) => { if (row.url().endsWith("/api/decide")) submissions.push(row.postDataJSON()); });
  await questionCard(page, retention).getByRole("button", { name: "14 days", exact: true }).click();
  await questionCard(page, region).getByRole("button", { name: "East", exact: true }).click();
  await expect(card.getByRole("button", { name: "Send 2 answers", exact: true })).toBeVisible();
  await expect(card.getByRole("button", { name: "Use recommendations", exact: true })).toHaveCount(0);
  expect(submissions).toHaveLength(0);
  expect((await readTask(request, slug)).question_group.questions.every((q) => q.status === "open")).toBe(true);
  await card.locator("..").evaluate((node) => node.scrollIntoView({ block: "start" }));
  await expect(questionCard(page, retention).getByText(retention.question, { exact: true })).toBeInViewport();
  await walk.state("review-03-group", {
    visible: [card.getByText(owner.question, { exact: true }), card.getByRole("button", { name: "Send 2 answers", exact: true })],
    hidden: [card.getByRole("button", { name: "Use recommendations", exact: true })],
  });
  // Deselecting the region leaves an explicit one-answer batch, never a default for the other questions.
  await questionCard(page, region).getByRole("button", { name: "East", exact: true }).click();
  await card.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await recorded(questionCard(page, retention));
  expect(submissions).toEqual([{ project: "atlas", slug, group_id: group.id, group_revision: group.revision,
    answers: [{ question_id: retention.id, revision: retention.revision, option_key: "fourteen" }] }]);
  let after = await readTask(request, slug);
  expect(after.question_group.questions.filter((q) => q.status === "open").map((q) => q.id)).toEqual([region.id, owner.id]);
  expect(after.question_group.questions[0]!.resolution?.option_key).toBe("fourteen");
  await card.getByRole("button", { name: "Use recommendations", exact: true }).click();
  await recorded(questionCard(page, region));
  after = await readTask(request, slug);
  expect(after.question_group.questions.filter((q) => q.status === "open").map((q) => q.id)).toEqual([owner.id]);
  expect(after.question_group.questions[0]!.resolution?.option_key).toBe("fourteen");
  expect(after.question_group.questions[1]!.resolution?.option_key).toBe("west");
  await expect(card.getByRole("button", { name: "Use recommendations", exact: true })).toHaveCount(0);
  await questionCard(page, owner).evaluate((node) => node.scrollIntoView({ block: "center" }));
  await expect(card.getByText(owner.question, { exact: true })).toBeInViewport();
  await walk.state("review-05-partial", { visible: [card.getByText(owner.question, { exact: true }), page.getByRole("textbox", { name: "Message the L2", exact: true })], hidden: [card.getByRole("button")] });
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
  expect((await (await request.get("/fixture/workers")).json()).calls).toHaveLength(1);
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
  await walk.state("group-follow-up-keeps-all-three-open", { visible: [card.getByText("3 questions to answer", { exact: true })], hidden: [card.getByText("Decision recorded", { exact: true })] });
  await send(page, "Keep 14 days; use snapshots so the backup region no longer matters.");
  expect((await readTask(request, slug)).question_group.questions.every((q) => q.status === "open")).toBe(true);
  const chosen = await modelCheckpoint(request, slug);
  after = await readTask(request, slug);
  expect(after.question_group.questions[0]!.resolution).toMatchObject({ disposition: "answered", message_id: chosen.message_id });
  expect(after.question_group.questions[1]!.resolution).toMatchObject({ disposition: "superseded", message_id: chosen.message_id });
  expect(after.question_group.questions[2]!.status).toBe("open");
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
  await walk.state("typed-answer-all-recorded", { visible: [card.getByText("Answers recorded", { exact: true })], hidden: [card.getByRole("button")] });
});

test("a stale group rejects the whole batch, refresh clears picks, and retry records one shared receipt", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, initial } = await createGroup(request);
  const group = initial.question_group;
  const [retention, region] = group.questions as [Question, Question, Question];
  await walk.open(atQuestion(slug, retention));
  const card = groupCard(page, group);
  await questionCard(page, retention).getByRole("button", { name: "14 days", exact: true }).click();
  await questionCard(page, region).getByRole("button", { name: "West", exact: true }).click();
  expect((await request.post("/fixture/revise-group", { data: { slug } })).ok()).toBe(true);
  const before = await readTask(request, slug);
  const staleResponse = page.waitForResponse((row) => row.url().endsWith("/api/decide"));
  await card.getByRole("button", { name: "Send 2 answers", exact: true }).click();
  expect((await staleResponse).status()).toBe(409);
  const failed = await readTask(request, slug);
  expect(failed.messages).toEqual(before.messages);
  expect(failed.question_group.questions.every((q) => q.status === "open")).toBe(true);
  expect((await (await request.get("/fixture/workers")).json()).calls).toHaveLength(0);
  await walk.state("stale-batch-accepts-no-members", { visible: [card.getByRole("alert"), card.getByRole("button", { name: "Refresh", exact: true })], hidden: [card.getByText("Decision recorded", { exact: true })] });
  await card.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(card.getByRole("alert")).toHaveCount(0);
  await expect(card.locator('[aria-pressed="true"]')).toHaveCount(0);
  await expect(card.getByRole("button", { name: "Use recommendations", exact: true })).toBeEnabled();
  const submitted = page.waitForRequest((row) => row.url().endsWith("/api/decide"));
  await card.getByRole("button", { name: "Use recommendations", exact: true }).click();
  const payload = (await submitted).postDataJSON();
  await expect(card.getByText("Decision recorded", { exact: true })).toHaveCount(2);
  expect((await request.post("/api/decide", { data: payload })).ok()).toBe(true);
  const after = await readTask(request, slug);
  const receipts = after.question_group.questions.filter((q) => q.status === "resolved").map((q) => q.resolution!);
  expect(receipts.map((row) => row.option_key)).toEqual(["seven", "east"]);
  expect(receipts[0]!.message_id).toBe(receipts[1]!.message_id);
  expect(after.messages.filter((m) => m.id === receipts[0]!.message_id)).toHaveLength(1);
  expect(after.question_group.questions.filter((q) => q.status === "open")).toHaveLength(1);
  await expect.poll(async () => (await readTask(request, slug)).state).toBe("running");
  expect((await (await request.get("/fixture/workers")).json()).calls).toHaveLength(1);
  await walk.state("refreshed-recommendations-recorded-once", { visible: [card.getByText("1 question to answer", { exact: true })], hidden: [card.getByRole("button")] });
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
  await walk.state("02-send-failed-draft-retained", {
    visible: [field, page.getByRole("alert").filter({ hasText: "Not sent." })],
    hidden: [page.getByRole("region", { name: "Task conversation", exact: true }).locator(".bubble").filter({ hasText: "Keep the draft after this failed send." })],
  });
  expect((await readTask(request, slug)).messages.some((row) => row.text === "Keep the draft after this failed send.")).toBe(false);
  await page.route("**/api/decide", (route) => route.fulfill({ status: 403, json: { error: "Write access denied." } }), { times: 1 });
  await card.getByRole("button", { name: q.recommendation!.label, exact: true }).click();
  await expect(card.getByRole("button", { name: q.recommendation!.label, exact: true })).toBeDisabled();
  await expect(field).toBeDisabled();
  await expect(field).toHaveValue("Keep the draft after this failed send.");
  await walk.state("03-denied-send-and-acceptance", {
    visible: [page.getByText("You cannot send or record a decision here.", { exact: false }), card],
    hidden: [card.getByText("Decision recorded", { exact: true })],
  });
  expect((await readTask(request, slug)).question?.status).toBe("open");
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(field).toBeEnabled();
  await expect(card.getByRole("button", { name: q.recommendation!.label, exact: true })).toBeEnabled();
  await walk.state("04-refresh-restores-writing", { visible: [field], hidden: [page.getByText("You cannot send or record a decision here.", { exact: false })] });
  await page.route(endpoint, (route) => route.fulfill({ status: 503, json: { error: "Question read unavailable." } }));
  await page.reload();
  const error = page.getByText(/Could not load the task/);
  await expect(error).toBeVisible({ timeout: 20_000 });
  await walk.state("05-initial-read-failed", { visible: [error, page.getByRole("button", { name: "Retry", exact: true }).first()], hidden: [field] });
  await page.unroute(endpoint);
  await page.getByRole("button", { name: "Retry", exact: true }).first().click();
  await walk.state("06-read-recovered", { visible: [card, field], hidden: [error] });
});
