import { expect, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "decision-selections-service.py" });
test.setTimeout(90_000);

// SPEC.md §3.8.2: a press shows on the operator's side as the choice it made, never as words they typed.
const retention = "How long should we keep the old index?";
const region = "Where should the backup live?";

function view(page: Page, info: TestInfo) {
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });

  return {
    phone: info.project.name === "phone",
    conversation,
    article: (title: string) => page.getByRole("article", { name: title, exact: true }),
    pill: (label: string, about: string) => conversation.locator(".choice-mine").filter({ has: page.locator(".choice-label", { hasText: label }) }).filter({ hasText: about }),
    typed: (text: string) => conversation.locator(".msg-row").filter({ has: page.locator(".bubble", { hasText: text }) }),
    bubble: (text: string) => conversation.locator(".msg-row .bubble").filter({ hasText: text }),
  };
}

async function fixture(request: APIRequestContext, path: string) {
  const response = await request.put(path);
  expect(response.ok(), `${path} must succeed`).toBe(true);
}

async function post(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(path, { data });
  expect(response.ok(), `${path} must succeed`).toBe(true);
}

test("quick answers show as the options pressed and follow their delivery to the recorded decision", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const v = view(page, info);
  const rollout = v.article("Index rollout");
  await walk.open("/");
  await walk.state("01-needs-you-offers-the-options", {
    visible: [rollout.getByText(retention), rollout.getByRole("button", { name: /7 days/ }), rollout.getByRole("button", { name: /West/ })],
    hidden: [rollout.locator(".choice")],
  });
  await rollout.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  const card = v.conversation.locator(".conversation-question");
  await card.getByRole("button", { name: /7 days/ }).click();
  const other = card.locator("[data-question-id]").filter({ hasText: region });
  await other.getByRole("button", { name: "Other…", exact: true }).click();
  await other.getByRole("textbox").fill("Use the north region.");
  const seven = v.pill("7 days", retention);
  const north = v.typed("Use the north region.");
  await walk.state("02-an-option-and-a-typed-answer-staged", {
    visible: [card.getByRole("button", { name: "Send 2 answers", exact: true })],
    hidden: [seven, north],
  });
  await card.getByRole("button", { name: "Send 2 answers", exact: true }).click();
  // The owner's resume delivers the answers at once, so the pill reaches its delivered check.
  await expect(seven).toHaveAttribute("data-state", "sent");
  await walk.state("03-answers-sent-as-a-pill-and-a-bubble", {
    visible: [seven, seven.getByText(retention, { exact: true }), north, north.getByText(region, { exact: true }),
      v.conversation.getByText("Sent · the L2 has your reply.")],
    hidden: [v.bubble("Keep the old index for seven days."), seven.getByRole("button", { name: "Remove" }), card.getByRole("button", { name: /14 days/ })],
  });
  await page.reload();
  await expect(seven).toHaveAttribute("data-state", "sent");
  await walk.state("03b-reload-renders-the-same-selection", {
    visible: [seven, north], hidden: [v.bubble("Keep the old index for seven days.")],
  });
  await walk.open("/");
  await walk.state("03c-needs-you-without-the-answered-task", {
    visible: [page.getByRole("heading", { name: "Needs you", exact: true })], hidden: [rollout],
  });

  await fixture(request, "/fixture/record");
  await walk.open("/projects/atlas/tasks/index-rollout");
  await expect(seven).toHaveAttribute("data-state", "done");
  await walk.state("04-decision-recorded-turns-green", {
    visible: [seven, north, seven.getByText("decision recorded")],
    hidden: [v.bubble("Keep the old index for seven days.")],
  });
  await expect(seven.getByText("decision recorded")).toHaveClass(/visually-hidden/);
});

test("Approve merge works in place in Needs you: sending, not sent, retry, then an approval in the chat", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const v = view(page, info);
  const release = v.article("Release notes");
  await walk.open("/");
  let fail: () => void = () => {};
  const failing = new Promise<void>((resolve) => { fail = resolve; });
  await page.route("**/api/l2/message", async (route) => {
    await failing;
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "The fixture refuses one send." }) });
  });
  const approve = release.getByRole("button", { name: "Approve merge", exact: true });
  await walk.state("05-review-offers-approve-merge", {
    visible: [approve, release.getByRole("link", { name: "View PR #42", exact: true })], hidden: [release.getByRole("alert")],
  });
  await approve.click();
  const sending = release.getByRole("button", { name: "Approve merge, sending", exact: true });
  await walk.state("06-approve-merge-sending", {
    visible: [sending, sending.locator(".spinner")],
    hidden: [approve, release.getByRole("alert")],
  });
  await expect(sending).toBeFocused();
  await expect(sending).toHaveAttribute("aria-disabled", "true");
  fail();
  const retry = release.getByRole("button", { name: "Retry, Approve merge", exact: true });
  await walk.state("07-not-sent-offers-retry", {
    visible: [retry, release.getByRole("alert").filter({ hasText: /^Not sent$/ }), release.getByRole("link", { name: "View PR #42", exact: true })],
    hidden: [sending],
  });
  await page.unroute("**/api/l2/message");
  const sent = page.waitForResponse((row) => new URL(row.url()).pathname === "/api/l2/message" && row.request().method() === "POST");
  await retry.click();
  const saved = await sent;
  expect(saved.ok()).toBe(true);
  expect(saved.request().postDataJSON().text).toBe("Approved: merge PR #42.");
  await walk.state("07b-approval-leaves-needs-you", {
    visible: [page.getByRole("heading", { name: "Needs you", exact: true })], hidden: [release],
  });

  await walk.open("/projects/atlas/tasks/release-notes");
  const approval = v.pill("Approve merge", "PR #42");
  await expect(approval).toHaveAttribute("data-state", "sent");
  await walk.state("08-approval-in-the-chat", {
    visible: [approval, approval.getByText("PR #42", { exact: true })],
    hidden: [v.bubble("Approved: merge PR #42."), v.conversation.getByRole("button", { name: "Approve merge", exact: true })],
  });
});

test("a typed approval waits with Remove, then shows unconfirmed and sent delivery", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const v = view(page, info);
  // The operator's exact approval words are an approval in the record, so typing them shows the action too.
  await walk.open("/projects/atlas/tasks/cache-warmup");
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  await field.fill("Approved: merge PR #7.");
  const saved = page.waitForResponse((row) => new URL(row.url()).pathname === "/api/l2/message" && row.request().method() === "POST");
  await v.conversation.getByRole("button", { name: "Send", exact: true }).click();
  expect((await saved).ok()).toBe(true);
  const first = v.pill("Approve merge", "PR #7");
  const remove = first.getByRole("button", { name: "Remove", exact: true });
  await walk.state("09-waiting-for-a-checkpoint-with-remove", {
    visible: [first, remove, v.conversation.getByRole("button", { name: /Send now/ })],
    hidden: [v.bubble("Approved: merge PR #7."), v.conversation.locator(".bubble-remove")],
  });
  await expect(first).toHaveAttribute("data-state", "wait");
  await walk.state("10-removed-selection-leaves-the-chat", { action: () => remove.click(), visible: [v.conversation], hidden: [first] });

  await post(request, "/api/l2/message", { project: "atlas", slug: "cache-warmup", text: "Approved: merge PR #8." });
  await fixture(request, "/fixture/take");
  await walk.open("/projects/atlas/tasks/cache-warmup");
  const second = v.pill("Approve merge", "PR #8");
  await walk.state("11-delivery-unconfirmed", {
    visible: [second, second.getByText("Delivery unconfirmed", { exact: true })],
    hidden: [second.getByRole("button", { name: "Remove" }), first],
  });
  await expect(second).toHaveAttribute("data-state", "wait");

  await fixture(request, "/fixture/receipt");
  await walk.open("/projects/atlas/tasks/cache-warmup");
  await expect(second).toHaveAttribute("data-state", "sent");
  await walk.state("12-delivered-shows-a-check", {
    visible: [second], hidden: [second.getByText("Delivery unconfirmed", { exact: true }), second.getByRole("button", { name: "Remove" })],
  });
});
