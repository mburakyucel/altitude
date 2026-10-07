import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "task-design-service.py" });
const slug = "conversation-layout";
const taskPath = `/projects/atlas/tasks/${slug}`;
type Question = { id: string; revision: number; status: string; design_url: string; design_title: string };
async function task(request: APIRequestContext) {
  const response = await request.get(`/api/task/atlas/${slug}`);
  expect(response.ok()).toBe(true);
  return response.json() as Promise<{ question: Question; question_group: { questions: Question[] }; hold_merge: string; messages: { text: string }[] }>;
}
const atQuestion = (q: Question) => `${taskPath}?question=${q.id}&revision=${q.revision}`;
const park = async (request: APIRequestContext) => expect((await request.post("/fixture/park")).ok()).toBe(true);
const card = (page: Page, q: Question) => page.getByRole("region", { name: "Task conversation", exact: true })
  .locator(`[data-question-id="${q.id}"][data-question-revision="${q.revision}"]`);

test("proposal v5 identifies its saved title across review entries at question revision 3", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const first = (await task(request)).question;
  expect((await request.post("/fixture/revise")).ok()).toBe(true);
  expect((await request.post("/fixture/proposal-v5")).ok()).toBe(true);
  const q = (await task(request)).question;
  const title = "Proposal v5: settings, model choice and menus";
  expect(q.revision).toBe(3);
  expect(q.design_title).toBe(title);
  expect((await request.post("/fixture/proposal-v5")).ok()).toBe(true);
  expect((await task(request)).question).toEqual(q);
  for (const [state, path] of [["needs-you", "/"], ["project-work", "/projects/atlas?tab=work"], ["task", atQuestion(q)]]) {
    await walk.open(path);
    if (state === "project-work") {
      await page.getByRole("region", { name: "Work", exact: true }).locator(`a[href="${atQuestion(q)}"]`).click();
    }
    const link = page.getByRole("link", { name: `View preview · ${title}`, exact: true });
    await link.scrollIntoViewIfNeeded();
    await walk.state(`v5-revision-3-${state}`, {
      visible: [link, page.getByText("Approve the tabbed design (v5)?", { exact: true })],
      hidden: [page.getByRole("link", { name: "View preview · v3", exact: true })],
    });
    const opened = page.waitForEvent("popup");
    await link.click();
    const preview = await opened;
    await expect(preview).toHaveURL(new RegExp(`${q.design_url}$`));
    await walkthrough(preview, info).state(`v5-viewer-from-${state}`, {
      visible: [preview.getByRole("heading", { name: title, exact: true })],
      hidden: [preview.getByText("Preview · v3", { exact: true })],
    });
    await preview.close();
  }
  await walk.open(first.design_url);
  await walk.state("v5-older-immutable-preview", {
    visible: [page.getByRole("heading", { name: "Conversation layout", exact: true }), page.getByText("Earlier preview.", { exact: false })],
    hidden: [page.getByRole("heading", { name: title, exact: true })],
  });
});

test("a saved proposal opens from chat, full size and back; a follow-up hands the turn back, is not approval, and approval retains the hold", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const initial = await task(request);
  const q = initial.question;
  await walk.open("/");
  await expect(page.getByRole("heading", { name: "Needs you", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "View preview · Conversation layout", exact: true })).toBeVisible();
  await walk.open(atQuestion(q));
  const link = card(page, q).getByRole("link", { name: "View preview · Conversation layout", exact: true });
  await walk.state("01-question-preview-entry", { visible: [link, card(page, q).getByRole("button", { name: "Use this design" })], hidden: [] });
  const popup = page.waitForEvent("popup");
  await link.click();
  const preview = await popup;
  const previewWalk = walkthrough(preview, info);
  await expect(preview).toHaveURL(new RegExp(`${q.design_url}$`));
  await expect(preview.getByRole("img", { name: "Phone conversation", exact: true })).toBeVisible();
  const fullSize = preview.getByRole("link", { name: "Open Phone conversation full size", exact: true });
  await previewWalk.state("02-saved-proposal", {
    visible: [preview.getByRole("main").getByRole("heading", { name: "Conversation layout", exact: true }), fullSize, preview.getByRole("link", { name: "← Back to question", exact: true })],
    hidden: [preview.getByText("Loading preview…", { exact: true }), preview.getByRole("button", { name: "Use this design" })],
  });
  expect((await task(request)).question.status).toBe("open");
  expect(await preview.evaluate(() => "proposalExecuted" in window)).toBe(false);
  await expect(preview.getByText("<script>window.proposalExecuted = true</script>", { exact: true })).toBeVisible();
  await preview.getByRole("region", { name: "Preview text", exact: true }).scrollIntoViewIfNeeded();
  await previewWalk.state("02b-readable-proposal-text", { visible: [preview.getByRole("region", { name: "Preview text", exact: true })], hidden: [preview.locator(".design-text script")] });
  const imagePopup = preview.waitForEvent("popup");
  await fullSize.click();
  const image = await imagePopup;
  await image.waitForLoadState();
  await expect(image.locator("img")).toBeVisible();
  await walkthrough(image, info).state("03-full-size-image", { visible: [image.locator("img")], hidden: [image.locator("script")] });
  await image.close();
  expect((await request.post("/fixture/edit-worktree")).ok()).toBe(true);
  await preview.reload();
  await expect(preview.getByRole("img", { name: "Phone conversation", exact: true })).toBeVisible();
  await expect(preview.getByText("Unpresented worktree edit", { exact: false })).toHaveCount(0);
  await preview.getByRole("link", { name: "← Back to question", exact: true }).click();
  await expect(preview).toHaveURL(new RegExp(`${taskPath}\\?question=${q.id}&revision=1$`));
  await expect(card(preview, q)).toBeInViewport();
  await preview.getByRole("textbox", { name: "Message the L2", exact: true }).fill("Could the reply have more room?");
  await preview.getByRole("region", { name: "Task conversation", exact: true }).getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(async () => (await task(request)).messages.some((m) => m.text === "Could the reply have more room?")).toBe(true);
  expect((await task(request)).question.status).toBe("open");
  const sent = preview.getByRole("region", { name: "Task conversation", exact: true }).getByRole("status").filter({ hasText: "Sent · the L2 has your reply." });
  await previewWalk.state("04-follow-up-hands-turn-back", {
    visible: [sent, preview.getByText("Could the reply have more room?", { exact: true })],
    hidden: [card(preview, q), preview.getByText("Decision recorded", { exact: true })],
  });
  await park(request);
  await previewWalk.state("04b-asked-again-decision-still-open", {
    visible: [preview.getByText("Your turn · 1 question · asked again", { exact: true }), card(preview, q).getByRole("button", { name: "Use this design" }), card(preview, q).getByRole("link", { name: "View preview · Conversation layout", exact: true })],
    hidden: [sent, card(preview, q).getByText("Decision recorded", { exact: true })],
  });
  await card(preview, q).getByRole("button", { name: "Use this design" }).click();
  await preview.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect(sent).toBeVisible();
  await expect(card(preview, q)).toHaveCount(0);
  expect((await task(request)).question.status).toBe("open");
  expect((await request.post("/fixture/checkpoint")).ok()).toBe(true);
  await preview.reload();
  await expect(card(preview, q).getByText("Decision recorded", { exact: true })).toBeVisible();
  expect((await task(request)).hold_merge).toBe(initial.hold_merge);
  await previewWalk.state("05-design-accepted-hold-retained", {
    visible: [card(preview, q).getByText("Decision recorded", { exact: true }), card(preview, q).getByRole("link", { name: "View preview · Conversation layout", exact: true })],
    hidden: [card(preview, q).getByRole("button", { name: "Use this design" })],
  });
  await preview.close();
});

test("a replacement labels the earlier saved proposal and returns to its exact question revision", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const q = (await task(request)).question;
  await walk.open(q.design_url);
  await expect(page.getByRole("img", { name: "Phone conversation", exact: true })).toBeVisible();
  expect((await request.post("/fixture/revise")).ok()).toBe(true);
  const current = (await task(request)).question;
  expect(current.id).toBe(q.id);
  expect(current.revision).toBe(q.revision + 1);
  await walk.state("earlier-fixed-version", {
    visible: [page.getByText("Earlier preview.", { exact: false }), page.getByRole("link", { name: "Open current question", exact: true })],
    hidden: [page.getByText("The revised proposal", { exact: false })],
  });
  await expect(page.getByRole("link", { name: "← Back to question", exact: true })).toHaveAttribute("href", atQuestion(q));
  await page.getByRole("link", { name: "← Back to question", exact: true }).click();
  await expect(card(page, q)).toBeVisible();
  await expect(card(page, q).getByRole("button", { name: "Use this design" })).toHaveCount(0);
  await walk.open(current.design_url);
  await walk.state("replacement-version", {
    visible: [page.getByRole("heading", { name: "Conversation layout", exact: true }), page.getByText("The revised proposal", { exact: false })],
    hidden: [page.getByText("Earlier preview.", { exact: false })],
  });
  expect((await task(request)).question.status).toBe("open");
});

test("the current implementation preview is discoverable from Needs you, Work and latest chat without confusing the approved proposal", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const proposal = (await task(request)).question;
  expect((await request.post("/fixture/implementation-review")).ok()).toBe(true);
  const initial = await task(request);
  const review = initial.question;
  expect(review.id).not.toBe(proposal.id);
  expect(review.revision).toBe(1);
  const previewName = "View preview · Conversation layout — implementation review";
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const draft = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const back = info.project.name === "phone" ? page.getByRole("button", { name: "Back", exact: true }) : page.locator(".task-crumb");
  async function inspectCurrent(link: Locator, state: string) {
    await expect(link).toBeInViewport();
    const opened = page.waitForEvent("popup");
    await link.click();
    const preview = await opened;
    await expect(preview).toHaveURL(new RegExp(`${review.design_url}$`));
    await walkthrough(preview, info).state(state, {
      visible: [preview.getByRole("heading", { name: "Conversation layout — implementation review", exact: true }), preview.getByRole("link", { name: "← Back to question", exact: true })],
      hidden: [preview.getByRole("button", { name: "Use this design", exact: true })],
    });
    await expect(preview.getByRole("link", { name: "← Back to question", exact: true })).toHaveAttribute("href", atQuestion(review));
    await preview.close();
    expect((await task(request)).question.status).toBe("open");
    expect((await task(request)).hold_merge).toBe(initial.hold_merge);
  }

  await walk.open("/");
  const inboxPreview = page.getByRole("link", { name: previewName, exact: true });
  await walk.state("review-01-needs-you-entry", { visible: [inboxPreview, page.getByText("Review the implemented conversation layout before merging?", { exact: true })], hidden: [conversation] });
  await inspectCurrent(inboxPreview, "review-02-from-inbox");
  await expect(page).toHaveURL("/");
  await page.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  await expect(page).toHaveURL(atQuestion(review));
  await expect(card(page, review)).toBeInViewport();
  await back.click();
  await expect(page).toHaveURL("/");

  await walk.open("/projects/atlas?tab=work");
  const work = page.getByRole("region", { name: "Work", exact: true });
  const row = work.locator(`a[href="${atQuestion(review)}"]`);
  await walk.state("review-03-work-entry", { visible: [row], hidden: [work.getByRole("link", { name: previewName, exact: true })] });
  await row.click();
  await expect(page).toHaveURL(atQuestion(review));
  await expect(card(page, review)).toBeInViewport();
  await inspectCurrent(card(page, review).getByRole("link", { name: previewName, exact: true }), "review-04-from-work-question");
  await back.click();
  await expect(page).toHaveURL("/projects/atlas?tab=work");
  await expect(work).toBeVisible();

  await walk.open(taskPath);
  const jumps = page.locator(".conversation-jumps");
  const currentPreview = jumps.getByRole("link", { name: previewName, exact: true });
  const pill = jumps.getByRole("button", { name: "Your turn · 1 question", exact: true });
  // The open question ends the chat, so the latest view already shows it.
  await expect(card(page, review)).toBeInViewport();
  await walk.state("review-05-latest-chat-ends-with-question", { visible: [card(page, review).getByRole("link", { name: previewName, exact: true })], hidden: [currentPreview, pill] });
  await conversation.locator(".convo-scroll").evaluate((node) => { node.scrollTop = 0; });
  await walk.state("review-05b-reading-back-keeps-question-jump", { visible: [pill], hidden: [currentPreview] });
  const jumpBox = (await pill.boundingBox())!;
  expect(jumpBox.height).toBeGreaterThanOrEqual(44);
  expect(jumpBox.width).toBeGreaterThanOrEqual(44);
  await expect(jumps.getByRole("button", { name: "Latest messages", exact: true })).toBeHidden();
  await expect(card(page, review)).not.toBeInViewport();
  await draft.fill("Does the implementation preserve my place when I return?");
  await pill.click();
  await expect(card(page, review)).toBeInViewport();
  await inspectCurrent(card(page, review).getByRole("link", { name: previewName, exact: true }), "review-06-from-question-jump");
  await expect(draft).toHaveValue("Does the implementation preserve my place when I return?");
  await expect(currentPreview).toBeHidden();
  await expect(pill).toBeHidden();

  await walk.open(atQuestion(proposal));
  const earlier = card(page, proposal);
  await expect(earlier.getByText("Decision recorded", { exact: true })).toBeVisible();
  const opened = page.waitForEvent("popup");
  await earlier.getByRole("link", { name: "View preview · Conversation layout", exact: true }).click();
  const savedProposal = await opened;
  await walkthrough(savedProposal, info).state("review-07-approved-proposal-provenance", {
    visible: [savedProposal.getByRole("main").getByRole("heading", { name: "Conversation layout", exact: true }), savedProposal.getByText("Keep the conversation easy to read. The question and its reply share one place.", { exact: true })],
    hidden: [savedProposal.getByRole("heading", { name: "Conversation layout — implementation review", exact: true })],
  });
  await expect(savedProposal).toHaveURL(new RegExp(`${proposal.design_url}$`));
  await expect(savedProposal.getByRole("link", { name: "← Back to question", exact: true })).toHaveAttribute("href", atQuestion(proposal));
  // This approved proposal belongs to a different decision, not an older revision of the review.
  await expect(savedProposal.getByRole("link", { name: "Open current question", exact: true })).toHaveCount(0);
  await savedProposal.close();
  const unchanged = await task(request);
  expect(unchanged.question.status).toBe("open");
  expect(unchanged.hold_merge).toBe(initial.hold_merge);
  expect(unchanged.messages).toEqual(initial.messages);
});

test("a grouped review keeps its current preview reachable after another member is answered and removes shortcuts after the final answer", async ({ page, request }, info) => {
  expect((await request.post("/fixture/implementation-review")).ok()).toBe(true);
  const review = (await task(request)).question;
  expect((await request.post("/fixture/group-review")).ok()).toBe(true);
  const initial = await task(request);
  const date = initial.question;
  expect(date.id).not.toBe(review.id);
  expect(initial.question_group.questions.map((q) => q.id)).toEqual([review.id, date.id]);
  const walk = walkthrough(page, info);
  await walk.open(taskPath);
  const jumps = page.locator(".conversation-jumps");
  const preview = jumps.getByRole("link", { name: "View preview · Conversation layout — implementation review", exact: true });
  const scroller = page.getByRole("region", { name: "Task conversation", exact: true }).locator(".convo-scroll");
  const readBack = () => scroller.evaluate((node) => { node.scrollTop = 0; });
  await expect(card(page, review)).toBeInViewport();
  await walk.state("group-review-01-two-open-members-end-the-chat", {
    visible: [card(page, review).getByRole("link", { name: "View preview · Conversation layout — implementation review", exact: true }), card(page, date)],
    hidden: [preview, jumps.getByRole("button", { name: /^Your turn/ })],
  });
  await readBack();
  await walk.state("group-review-01b-reading-back-shows-question-jump", { visible: [jumps.getByRole("button", { name: "Your turn · 2 questions", exact: true })], hidden: [preview] });
  expect((await request.post("/fixture/resolve-question", { data: { id: date.id } })).ok()).toBe(true);
  await expect.poll(async () => (await task(request)).question.status).toBe("resolved");
  // Answering one member in chat hands the turn back; the owner asks the review again.
  await park(request);
  await page.reload();
  await readBack();
  const viewQuestion = jumps.getByRole("button", { name: "Your turn · 1 question", exact: true });
  await walk.state("group-review-02-question-survives-partial-answer", { visible: [viewQuestion], hidden: [preview] });
  await viewQuestion.click();
  await expect(card(page, review)).toBeInViewport();
  const opened = page.waitForEvent("popup");
  await card(page, review).getByRole("link", { name: "View preview · Conversation layout — implementation review", exact: true }).click();
  const saved = await opened;
  await expect(saved).toHaveURL(new RegExp(`${review.design_url}$`));
  await expect(saved.getByRole("heading", { name: "Conversation layout — implementation review", exact: true })).toBeVisible();
  await saved.close();
  await expect(card(page, review)).toBeInViewport();
  // The answered member folds behind the open review until the group closes.
  await expect(card(page, date).getByText("Decision recorded", { exact: true })).toBeHidden();
  await page.getByRole("region", { name: "Task conversation", exact: true }).getByText("1 earlier question", { exact: true }).click();
  await expect(card(page, date).getByText("Decision recorded", { exact: true })).toBeVisible();
  expect((await request.post("/fixture/resolve-question", { data: { id: review.id } })).ok()).toBe(true);
  await walk.state("group-review-03-final-answer-removes-shortcuts", {
    visible: [card(page, review).getByText("Decision recorded", { exact: true }), card(page, review).getByRole("link", { name: "View preview · Conversation layout — implementation review", exact: true })],
    hidden: [preview, viewQuestion],
  });
  expect((await task(request)).hold_merge).toBe(initial.hold_merge);
});

test("proposal and screenshot loading, unavailable, denied and failed reads recover explicitly", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const q = (await task(request)).question;
  const endpoint = `**/api/design/atlas/${slug}/${q.id}/${q.revision}`;
  const heading = page.getByRole("main").getByRole("heading", { name: "Conversation layout", exact: true });
  const unavailable = page.getByRole("heading", { name: "Design unavailable", exact: true });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(endpoint, async (route) => { await gate; await route.continue(); }, { times: 1 });
  await walk.open(q.design_url);
  try {
    await walk.state("loading-proposal", { visible: [page.getByText("Loading preview…", { exact: true })], hidden: [heading] });
  } finally { release(); }
  await expect(heading).toBeVisible();
  const images = "**/design/atlas/tasks/**";
  await page.route(images, (route) => route.fulfill({ status: 404, body: "Unavailable" }));
  await page.reload();
  const firstImage = page.getByRole("figure", { name: "Phone conversation", exact: true });
  await walk.state("screenshot-unavailable", {
    visible: [firstImage.getByText("Screenshot unavailable.", { exact: false }), firstImage.getByRole("button", { name: "Retry screenshot" })],
    hidden: [firstImage.getByRole("img"), firstImage.getByRole("link", { name: "Full size ↗", exact: true })],
  });
  await page.unroute(images);
  let releaseImage!: () => void;
  const imageGate = new Promise<void>((resolve) => { releaseImage = resolve; });
  await page.route(images, async (route) => { await imageGate; await route.continue(); }, { times: 1 });
  await firstImage.getByRole("button", { name: "Retry screenshot" }).click();
  try {
    await walk.state("loading-screenshot", { visible: [firstImage.getByText("Loading screenshot…", { exact: true })], hidden: [firstImage.getByRole("button", { name: "Retry screenshot" })] });
  } finally { releaseImage(); }
  await walk.state("screenshot-recovered", { visible: [firstImage.getByRole("img")], hidden: [firstImage.getByRole("button", { name: "Retry screenshot" }), firstImage.getByText("Loading screenshot…", { exact: true })] });
  for (const status of [403, 503, 409]) {
    await page.route(endpoint, (route) => route.fulfill({ status, json: { error: "Fixture read failure" } }));
    await page.reload();
    await walk.state(`proposal-read-${status}`, {
      visible: [unavailable, page.getByRole("button", { name: "Retry", exact: true }), page.getByRole("link", { name: "← Back to question", exact: true })],
      hidden: [heading, page.getByRole("img")],
    });
    await page.unroute(endpoint);
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(heading).toBeVisible();
  }
  await walk.state("proposal-recovered", { visible: [heading, firstImage.getByRole("img")], hidden: [unavailable, page.getByRole("button", { name: "Retry", exact: true })] });
  expect((await request.post("/fixture/damage-snapshot")).ok()).toBe(true);
  await walk.state("changed-snapshot-unavailable", { visible: [unavailable, page.getByRole("button", { name: "Retry", exact: true })], hidden: [heading, page.getByRole("img")] });
  await walk.open(q.design_url.replace(/\/1$/, "/99"));
  await walk.state("missing-proposal", { visible: [unavailable, page.getByRole("link", { name: "← Back to question", exact: true })], hidden: [heading, page.getByRole("img")] });
  expect((await task(request)).question.status).toBe("open");
});
