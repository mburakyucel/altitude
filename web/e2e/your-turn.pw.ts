import { expect, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "your-turn-service.py" });
test.setTimeout(90_000);

const question = "How long should we keep the old index?";
const followUp = "Could we roll back after day seven?";

function view(page: Page, info: TestInfo) {
  const phone = info.project.name === "phone";
  const primary = page.getByRole("navigation", { name: phone ? "Primary" : "Rail", exact: true });
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  return {
    phone,
    conversation,
    main: page.getByRole("main"),
    badge: (count: number) => primary.getByRole("link", { name: /Needs you/ }).locator(".badge").filter({ hasText: new RegExp(`^${count}$`) }),
    article: (title: string) => page.getByRole("article", { name: title, exact: true }),
    turn: conversation.locator(".conversation-turn"),
    card: conversation.locator(".conversation-question").filter({ hasText: question }),
    quiet: conversation.getByRole("status").filter({ hasText: "Sent · the L2 has your reply." }),
    field: page.getByRole("textbox", { name: "Message the L2", exact: true }),
    send: conversation.getByRole("button", { name: "Send", exact: true }),
    bubble: (text: string) => conversation.locator(".bubble").filter({ hasText: text }),
    hint: page.getByText("Replying hands the turn back to the L2.", { exact: true }),
  };
}

async function park(request: APIRequestContext) {
  const response = await request.post("/fixture/park");
  expect(response.ok()).toBe(true);
  const group = await response.json() as { questions: { status: string; asked_again?: boolean }[] };
  expect(group.questions.filter((q) => q.status === "open").map((q) => q.asked_again)).toEqual([true]);
}

test("a question at the end of the chat: reply hands the turn back, asked again, send failure", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const v = view(page, info);
  const rollout = v.article("Index rollout");
  const release = v.article("Release notes");
  await walk.open("/");
  await walk.state("01-needs-you-question-and-review", {
    visible: [page.getByRole("heading", { name: "Needs you", exact: true }), rollout.getByText(question),
      release.getByText("Review before merge", { exact: true }), release.getByRole("button", { name: "Approve merge", exact: true }),
      release.getByRole("link", { name: "View PR #42", exact: true }), v.badge(3)],
    hidden: [v.article("Cache warmup"), v.article("Retry policy")],
  });

  await rollout.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  await expect(v.card).toBeInViewport();
  // The open question follows the latest L2 message: it is the end of the conversation, not an old anchor.
  await expect(v.conversation.locator(".msg-row:has-text('the window is your call.') ~ .conversation-question")).toHaveCount(1);
  await walk.state("02-question-at-end-of-chat", {
    visible: [v.turn.getByText("Your turn · 1 question", { exact: true }), v.card.getByRole("button", { name: /7 days/ }),
      v.main.getByText("Your turn · 1 question").first(), ...(v.phone ? [] : [v.hint])],
    hidden: [v.quiet, v.conversation.getByRole("button", { name: /Your turn · 1 question/ })],
  });

  const pill = page.getByRole("button", { name: "Your turn · 1 question", exact: true });
  await walk.state("02b-scrolled-up-jump-pill", {
    action: () => page.locator(".convo-scroll").evaluate((node) => { node.scrollTop = 0; }),
    visible: [pill, v.conversation.getByText("The new index passes the fixture checks.", { exact: true })],
    hidden: [],
  });
  await pill.click();
  await expect(v.card).toBeInViewport();
  await walk.state("02c-pill-jumps-back", { visible: [v.card], hidden: [pill] });

  await walk.state("03-typing-follow-up", {
    action: () => v.field.fill(followUp),
    visible: [v.send, v.card],
    hidden: [v.quiet],
  });
  const saved = page.waitForResponse((row) => new URL(row.url()).pathname === "/api/l2/message" && row.request().method() === "POST");
  await v.send.click();
  expect((await saved).ok()).toBe(true);
  await walk.state("04-sent-handed-back", {
    visible: [v.quiet, v.bubble(followUp), v.main.getByText("L2 replying to you").first()],
    hidden: [v.card, v.turn, v.main.getByText(/Your turn · 1 question/)],
  });
  await walk.open("/");
  await walk.state("04b-needs-you-without-the-question", {
    visible: [page.getByRole("heading", { name: "Needs you", exact: true }), release, v.badge(2)],
    hidden: [rollout],
  });

  await park(request);
  await walk.open("/projects/atlas/tasks/index-rollout");
  await walk.state("05-asked-again-after-re-park", {
    visible: [v.turn.getByText("Your turn · 1 question · asked again", { exact: true }), v.card, v.bubble(followUp), v.badge(3)],
    hidden: [v.quiet],
  });

  let refused = false;
  await page.route("**/api/l2/message", async (route) => {
    if (refused) return route.fallback();
    refused = true;
    return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "The fixture refuses one send." }) });
  });
  const retryText = "Keep seven days unless rollback needs more.";
  await v.field.fill(retryText);
  const alert = v.conversation.getByRole("alert").filter({ hasText: "Not sent." });
  await walk.state("06-send-failed-question-stays", {
    action: () => v.send.click(),
    visible: [alert, alert.getByRole("button", { name: "Retry", exact: true }), v.card,
      v.turn.getByText("Your turn · 1 question · asked again", { exact: true }), v.badge(3)],
    hidden: [v.quiet, v.bubble(retryText)],
  });
  await expect(v.field).toHaveValue(retryText);
  const retried = page.waitForResponse((row) => new URL(row.url()).pathname === "/api/l2/message" && row.status() === 200);
  await alert.getByRole("button", { name: "Retry", exact: true }).click();
  await retried;
  await walk.state("06b-retry-hands-turn-back", {
    visible: [v.quiet, v.bubble(retryText), v.badge(2)],
    hidden: [alert, v.card],
  });
});

test("answering on a phone keeps the page scale: fields stay at 16px, drafts and reach are kept", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const v = view(page, info);
  await walk.open("/");
  await v.article("Index rollout").getByRole("link", { name: "Open L2 chat", exact: true }).click();
  await v.card.getByRole("button", { name: "Other…", exact: true }).click();
  const answer = v.card.getByRole("textbox", { name: /Your answer to:/ });
  await expect(answer).toBeFocused();
  await answer.pressSequentially("Keep it until the rollback drill passes.");
  const fontSize = (field: typeof answer) => field.evaluate((node) => getComputedStyle(node).fontSize);
  const scale = () => page.evaluate(() => window.visualViewport?.scale ?? 1);
  // Touch layouts keep fields at 16px so WebKit does not zoom on focus; desktop keeps its compact type.
  if (v.phone) for (const field of [answer, v.field]) expect(await fontSize(field)).toBe("16px");
  else expect(await fontSize(answer)).toBe("14px");
  await walk.state("10-typing-an-answer-keeps-scale", { visible: [answer, v.card.getByRole("button", { name: "Send 1 answer", exact: true })], hidden: [] });
  expect(await scale()).toBe(1);
  await v.field.click();
  await v.field.fill("A draft for the L2.");
  await walk.state("10b-switching-to-the-composer-keeps-the-answer", { visible: [v.field, v.send], hidden: [] });
  expect(await scale()).toBe(1);
  await expect(answer).toHaveValue("Keep it until the rollback drill passes.");
  await page.keyboard.press("Escape");
  await answer.click();
  await expect(answer).toBeInViewport();
  await expect(v.field).toHaveValue("A draft for the L2.");
  expect(await scale()).toBe(1);
});

test("a held PR waits for review before merge in the chat; approval hands it back", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const v = view(page, info);
  const review = v.conversation.locator("[data-review-pr='42']");
  const approve = review.getByRole("button", { name: "Approve merge", exact: true });
  const receipt = review.getByText("Approval sent · the L2 merges after a final check of the same PR.", { exact: true });
  await walk.open("/projects/atlas/tasks/release-notes");
  await expect(review.getByRole("link", { name: "View PR #42", exact: true })).toHaveAttribute("href", "https://github.com/example/atlas/pull/42");
  await walk.state("07-review-before-merge-in-chat", {
    visible: [v.conversation.getByText("Your turn · review before merge", { exact: true }),
      review.getByText("Review PR #42 before merge", { exact: true }),
      review.getByText("Operator review of the published release notes", { exact: true }), approve,
      v.main.getByText("Your turn · review PR #42").first(), v.badge(3)],
    hidden: [receipt, v.card],
  });
  const sent = page.waitForResponse((row) => new URL(row.url()).pathname === "/api/l2/message" && row.request().method() === "POST");
  await approve.click();
  expect((await sent).request().postDataJSON().text).toBe("Approved: merge PR #42 at 5f0c2e9.");
  await walk.state("08-approval-sent", {
    visible: [v.bubble("Approved: merge PR #42 at 5f0c2e9."), v.main.getByText("L2 replying to you").first(), v.conversation.getByText("Sent · the L2 has your reply."), v.badge(2)],
    hidden: [approve, v.conversation.getByText("Your turn · review before merge", { exact: true })],
  });
  await walk.open("/");
  await walk.state("08b-review-leaves-needs-you", {
    visible: [page.getByRole("heading", { name: "Needs you", exact: true }), v.article("Index rollout"), v.badge(2)],
    hidden: [v.article("Release notes")],
  });
});

test("Work rows say whose turn it is", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const v = view(page, info);
  const work = page.getByRole("region", { name: "Work", exact: true });
  const row = (title: string) => work.getByRole("link", { name: new RegExp(`^${title} ·`) });
  await walk.open("/projects/atlas?tab=work");
  await walk.state("09-work-labels", {
    visible: [
      row("Index rollout").getByText("Your turn · 1 question", { exact: true }),
      row("Release notes").getByText("Your turn · review PR #42", { exact: true }),
      row("Cache warmup").getByText(/^L2 working/),
      row("Retry policy").getByText("L2 replying to you", { exact: true }),
      row("Repair checkout").getByText("Paused · Checkout unavailable.", { exact: true }),
      row("Stopped validation").getByText("Stopped by you", { exact: true }),
    ],
    hidden: [row("Cache warmup").getByText(/Your turn/), row("Retry policy").getByText(/Your turn/), work.getByRole("article")],
  });
  await row("Repair checkout").click();
  await walk.state("09b-fault-header", {
    visible: [v.main.getByText("Paused · fault").first()],
    hidden: [v.turn, v.conversation.getByText(/Your turn/)],
  });
});
