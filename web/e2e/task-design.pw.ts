import { expect, type APIRequestContext, type Locator, type Page, type TestInfo } from "@playwright/test";
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
/** A preview's single Back control: the phone header's, or the page's own on desktop. */
const previewBack = (page: Page, info: TestInfo) =>
  page.getByRole("button", { name: info.project.name === "phone" ? "Back" : "← Back", exact: true });
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
    const origin = page.url();
    await link.click();
    await expect(page).toHaveURL(new RegExp(`${q.design_url}$`));
    await walk.state(`v5-viewer-from-${state}`, {
      visible: [page.getByRole("heading", { name: title, exact: true }), previewBack(page, info)],
      hidden: [page.getByText("Preview · v3", { exact: true })],
    });
    // The phone header carries the only Back control there.
    if (info.project.name === "phone") await expect(page.getByRole("button", { name: "← Back", exact: true })).toHaveCount(0);
    await previewBack(page, info).click();
    await expect(page).toHaveURL(origin);
  }
  await walk.open(first.design_url);
  await walk.state("v5-older-immutable-preview", {
    visible: [page.getByRole("main").getByRole("heading", { name: "Conversation layout", exact: true }), page.getByText("Earlier preview.", { exact: false })],
    hidden: [page.getByRole("heading", { name: title, exact: true })],
  });
});

test("maximum-length unbroken titles wrap in review cards and the viewer", async ({ page, request }, info) => {
  expect((await request.post("/fixture/long-title")).ok()).toBe(true);
  const q = (await task(request)).question;
  const title = "Settings".repeat(20);
  const walk = walkthrough(page, info);
  for (const [state, path] of [["needs-you", "/"], ["task", atQuestion(q)], ["viewer", q.design_url]]) {
    await walk.open(path);
    const label = state === "viewer" ? page.getByRole("heading", { name: title, exact: true })
      : page.getByRole("link", { name: `View preview · ${title}`, exact: true });
    await label.scrollIntoViewIfNeeded();
    await walk.state(`long-title-${state}`, { visible: [label], hidden: [] });
    const box = (await label.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    expect(await label.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  }
});

test("project preview returns through its question to L3 without a history loop", async ({ page, request }, info) => {
  expect((await request.post("/fixture/project-preview")).ok()).toBe(true);
  const initial = await task(request);
  const q = initial.question;
  await walkthrough(page, info).open("/projects/atlas");
  const opened = page.waitForEvent("popup");
  await page.getByRole("link", { name: "Review the layout", exact: true }).click();
  const preview = await opened;
  await expect(preview).toHaveURL(q.design_url);
  await preview.reload();
  await previewBack(preview, info).click();
  await expect(preview).toHaveURL(atQuestion(q));
  await expect(card(preview, q)).toBeInViewport();
  await preview.getByRole("button", { name: "Back", exact: true }).click();
  await expect(preview).toHaveURL("/projects/atlas");
  await walkthrough(preview, info).state("navigation-project-return", {
    visible: [preview.getByRole("link", { name: "Review the layout", exact: true })],
    hidden: [previewBack(preview, info), card(preview, q)],
  });
  await expect(page).toHaveURL("/projects/atlas");
  expect((await task(request)).question.status).toBe("open");
  expect((await task(request)).hold_merge).toBe(initial.hold_merge);
  await preview.close();
});

test("same-tab preview and question preserve browser Back and Forward", async ({ page, request }, info) => {
  expect((await request.post("/fixture/project-preview")).ok()).toBe(true);
  const q = (await task(request)).question;
  await walkthrough(page, info).open("/projects/atlas");
  const link = page.getByRole("link", { name: "Review the layout", exact: true });
  // Exercise opening the ordinary anchor in this tab, without synthesizing router state.
  await link.evaluate((node) => node.removeAttribute("target"));
  await link.click();
  await expect(page).toHaveURL(q.design_url);
  await page.goBack();
  await expect(page).toHaveURL("/projects/atlas");
  await page.goForward();
  await expect(page).toHaveURL(q.design_url);
  // A message link loads a new document, so no app history lies behind the preview: Back opens its question.
  await previewBack(page, info).click();
  await expect(page).toHaveURL(atQuestion(q));
  await page.goBack();
  await expect(page).toHaveURL("/projects/atlas");
  await page.goForward();
  await expect(page).toHaveURL(atQuestion(q));
  await page.reload();
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page).toHaveURL("/projects/atlas");
  // The direct-document fallback replaces the task with the project. Browser history
  // still contains the original project document; neither direction revives the preview.
  await page.goBack();
  await expect(page).toHaveURL("/projects/atlas");
  await page.goForward();
  await expect(page).toHaveURL("/projects/atlas");
});

test("a denied preview returns to its exact question and then the project", async ({ page, request }, info) => {
  const q = (await task(request)).question;
  await page.route(`**/api/design/atlas/${slug}/${q.id}/${q.revision}`, (route) =>
    route.fulfill({ status: 403, json: { error: "Fixture denied preview" } }));
  await walkthrough(page, info).open(q.design_url);
  await expect(page.getByRole("heading", { name: "Design unavailable", exact: true })).toBeVisible();
  await previewBack(page, info).click();
  await expect(page).toHaveURL(atQuestion(q));
  await expect(card(page, q)).toBeInViewport();
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page).toHaveURL("/projects/atlas");
});

test("task-origin preview returns to its question with the typed answer and message intact, without a loop", async ({ page, request }, info) => {
  const q = (await task(request)).question;
  const walk = walkthrough(page, info);
  await walk.open("/projects/atlas?tab=work");
  await page.getByRole("region", { name: "Work", exact: true }).locator(`a[href="${atQuestion(q)}"]`).click();
  const message = page.getByRole("textbox", { name: "Message the L2", exact: true });
  await message.fill("Keep this unsent question.");
  await card(page, q).getByRole("button", { name: "Other…", exact: true }).click();
  const answer = card(page, q).getByRole("textbox");
  await answer.fill("Use the layout, with more room for replies.");
  await card(page, q).getByRole("link", { name: "View preview · Conversation layout", exact: true }).click();
  await expect(page).toHaveURL(q.design_url);
  await previewBack(page, info).click();
  await expect(page).toHaveURL(atQuestion(q));
  await walk.state("task-return-keeps-drafts", {
    visible: [card(page, q), answer, page.getByRole("button", { name: "Send 1 answer", exact: true })],
    hidden: [page.getByRole("region", { name: "Preview text", exact: true })],
  });
  await expect(card(page, q)).toBeInViewport();
  await expect(answer).toHaveValue("Use the layout, with more room for replies.");
  await expect(message).toHaveValue("Keep this unsent question.");
  // Browser Forward and Back repeat the same round trip.
  await page.goForward();
  await expect(page).toHaveURL(q.design_url);
  await page.goBack();
  await expect(answer).toHaveValue("Use the layout, with more room for replies.");
  await expect(message).toHaveValue("Keep this unsent question.");
  // The task's Back leaves for where the task was opened, never into the preview again.
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page).toHaveURL("/projects/atlas?tab=work");
  await page.goForward();
  await expect(page).toHaveURL(atQuestion(q));
  await expect(answer).toHaveValue("Use the layout, with more room for replies.");
  // After a reload the preview still returns through app history; drafts were held in memory only.
  await card(page, q).getByRole("link", { name: "View preview · Conversation layout", exact: true }).click();
  await page.reload();
  await previewBack(page, info).click();
  await expect(page).toHaveURL(atQuestion(q));
  await expect(card(page, q)).toBeInViewport();
  await expect(message).toHaveValue("");
  await expect(card(page, q).getByRole("textbox")).toHaveCount(0);
  // A fresh visit to the same question starts empty.
  await walk.open(atQuestion(q));
  await expect(message).toHaveValue("");
  await expect(card(page, q).getByRole("textbox")).toHaveCount(0);
  expect((await task(request)).question.status).toBe("open");
});

test("Needs you preview returns to the list with the card in view and its draft intact", async ({ page, request }, info) => {
  const q = (await task(request)).question;
  const walk = walkthrough(page, info);
  await walk.open("/");
  const decision = page.getByRole("region", { name: "Project atlas", exact: true }).locator(`[data-question-id="${q.id}"][data-question-revision="${q.revision}"]`);
  await decision.getByRole("button", { name: "Other…", exact: true }).click();
  await decision.getByRole("textbox").fill("Looks right; ship it after the hold.");
  await decision.getByRole("link", { name: "View preview · Conversation layout", exact: true }).click();
  await expect(page).toHaveURL(q.design_url);
  await previewBack(page, info).click();
  await expect(page).toHaveURL("/");
  await walk.state("needs-you-return-keeps-draft", {
    visible: [page.getByRole("heading", { name: "Needs you", exact: true }), decision.getByRole("textbox")],
    hidden: [page.getByRole("region", { name: "Preview text", exact: true })],
  });
  await expect(decision).toBeInViewport();
  await expect(decision.getByRole("textbox")).toHaveValue("Looks right; ship it after the hold.");
  // An answer sent from another device while the preview is open drops the draft on return.
  await page.goForward();
  expect((await request.post("/fixture/resolve-question", { data: { id: q.id } })).ok()).toBe(true);
  await page.goBack();
  await expect(page).toHaveURL("/");
  await expect(page.getByText("Looks right; ship it after the hold.", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("textbox")).toHaveCount(0);
});

for (const current of [false, true]) {
  test(`direct earlier preview returns to the ${current ? "current" : "captured"} question without a loop`, async ({ page, request }, info) => {
    const q = (await task(request)).question;
    expect((await request.post("/fixture/revise")).ok()).toBe(true);
    const latest = (await task(request)).question;
    await walkthrough(page, info).open(q.design_url);
    await page.reload();
    await expect(page.getByText("Earlier preview.", { exact: false })).toBeVisible();
    await (current ? page.getByRole("link", { name: "Open current question", exact: true }) : previewBack(page, info)).click();
    const target = current ? latest : q;
    await expect(page).toHaveURL(atQuestion(target));
    await expect(card(page, target)).toBeInViewport();
    await page.getByRole("button", { name: "Back", exact: true }).click();
    await expect(page).toHaveURL("/projects/atlas");
    expect((await task(request)).question.status).toBe("open");
  });
}

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
  await link.click();
  const preview = page;
  const previewWalk = walk;
  await expect(preview).toHaveURL(new RegExp(`${q.design_url}$`));
  await expect(preview.getByRole("img", { name: "Phone conversation", exact: true })).toBeVisible();
  const fullSize = preview.getByRole("link", { name: "Open Phone conversation full size", exact: true });
  await previewWalk.state("02-saved-proposal", {
    visible: [preview.getByRole("main").getByRole("heading", { name: "Conversation layout", exact: true }), fullSize, previewBack(preview, info)],
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
  await previewBack(preview, info).click();
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
  await previewBack(page, info).click();
  await expect(page).toHaveURL(atQuestion(q));
  await expect(card(page, q)).toBeVisible();
  await expect(card(page, q).getByRole("button", { name: "Use this design" })).toHaveCount(0);
  await walk.open(current.design_url);
  await walk.state("replacement-version", {
    visible: [page.getByRole("main").getByRole("heading", { name: "Conversation layout", exact: true }), page.getByText("The revised proposal", { exact: false })],
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
    const origin = page.url();
    await link.click();
    await expect(page).toHaveURL(new RegExp(`${review.design_url}$`));
    await walk.state(state, {
      visible: [page.getByRole("heading", { name: "Conversation layout — implementation review", exact: true }), previewBack(page, info)],
      hidden: [page.getByRole("button", { name: "Use this design", exact: true })],
    });
    await previewBack(page, info).click();
    await expect(page).toHaveURL(origin);
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
  await expect(card(page, review)).toBeInViewport();
  await expect(draft).toHaveValue("Does the implementation preserve my place when I return?");
  await expect(currentPreview).toBeHidden();
  await expect(pill).toBeHidden();

  // An earlier question opened on the plain task address is where Back returns, not the latest messages.
  await walk.open(taskPath);
  await conversation.getByText("Earlier question · decision recorded", { exact: true }).click();
  const earlier = card(page, proposal);
  await earlier.scrollIntoViewIfNeeded();
  await earlier.getByRole("link", { name: "View preview · Conversation layout", exact: true }).click();
  await walk.state("review-07-approved-proposal-provenance", {
    visible: [page.getByRole("main").getByRole("heading", { name: "Conversation layout", exact: true }), page.getByText("Keep the conversation easy to read. The question and its reply share one place.", { exact: true })],
    hidden: [page.getByRole("heading", { name: "Conversation layout — implementation review", exact: true })],
  });
  await expect(page).toHaveURL(new RegExp(`${proposal.design_url}$`));
  // This approved proposal belongs to a different decision, not an older revision of the review.
  await expect(page.getByRole("link", { name: "Open current question", exact: true })).toHaveCount(0);
  await previewBack(page, info).click();
  await expect(page).toHaveURL(taskPath);
  await walk.state("review-08-back-to-earlier-question", { visible: [earlier.getByText("Decision recorded", { exact: true })], hidden: [] });
  await expect(earlier).toBeInViewport();
  await expect(card(page, review)).not.toBeInViewport();
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
  // Read back only once the reloaded conversation has opened at its question, as before the reload.
  await expect(card(page, review)).toBeInViewport();
  await readBack();
  const viewQuestion = jumps.getByRole("button", { name: "Your turn · 1 question", exact: true });
  await walk.state("group-review-02-question-survives-partial-answer", { visible: [viewQuestion], hidden: [preview] });
  await viewQuestion.click();
  await expect(card(page, review)).toBeInViewport();
  await card(page, review).getByRole("link", { name: "View preview · Conversation layout — implementation review", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${review.design_url}$`));
  await expect(page.getByRole("heading", { name: "Conversation layout — implementation review", exact: true })).toBeVisible();
  await previewBack(page, info).click();
  await expect(page).toHaveURL(taskPath);
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
      visible: [unavailable, page.getByRole("button", { name: "Retry", exact: true }), previewBack(page, info)],
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
  await walk.state("missing-proposal", { visible: [unavailable, previewBack(page, info)], hidden: [heading, page.getByRole("img")] });
  expect((await task(request)).question.status).toBe("open");
});
