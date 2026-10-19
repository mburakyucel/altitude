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

/** Whether an element matching `selector` appeared since the watch began: a card that leaves at the press never comes back. */
async function watch(page: Page, selector: string) {
  await page.evaluate((selector) => {
    const flags = window as unknown as { returned?: boolean };
    flags.returned = false;
    new MutationObserver(() => { if (document.querySelector(selector)) flags.returned = true; })
      .observe(document.body, { childList: true, subtree: true });
  }, selector);
  return () => page.evaluate(() => (window as unknown as { returned?: boolean }).returned);
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
  // Altitude holds the answers until the walk lets them through; the chat shows them at the press (SPEC.md §1.1).
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/decide", async (route) => { await held; await route.continue(); });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await card.getByRole("button", { name: "Send 2 answers", exact: true }).click();
  await walk.state("02b-answers-wait-at-the-press", {
    visible: [seven, seven.getByText(retention, { exact: true }), north, north.getByText(region, { exact: true })],
    hidden: [card, v.bubble("Keep the old index for seven days."), seven.getByRole("button", { name: "Remove" })],
  });
  await expect(seven).toHaveAttribute("data-state", "wait");
  expect(await seven.evaluate((node) => getComputedStyle(node.closest(".msg-row")!).animationName)).toBe("none");
  const pill = await seven.elementHandle();
  const rows = v.conversation.locator(".msg-row");
  await rows.evaluateAll((nodes) => { (window as unknown as { rows: Element[] }).rows = nodes; });
  const saved = page.waitForResponse((row) => new URL(row.url()).pathname === "/api/decide");
  release();
  expect((await saved).ok()).toBe(true);
  // The owner's resume delivers the answers at once, so the pill reaches its delivered check in place.
  await expect(seven).toHaveAttribute("data-state", "sent");
  expect(await seven.evaluate((node, earlier) => node === earlier, pill), "the saved answer settles the same pill").toBe(true);
  // The same rows, in the same order, are the same elements: nothing was replaced, moved or added twice.
  expect(await rows.evaluateAll((nodes) => {
    const earlier = (window as unknown as { rows: Element[] }).rows;
    return earlier.length === nodes.length && earlier.every((node, index) => node === nodes[index]);
  })).toBe(true);
  await page.unroute("**/api/decide");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect(v.conversation.getByText("Sent · the L2 has your reply.")).toHaveClass("sr-only");
  await expect(card).toHaveCount(0);
  await walk.state("03-answers-sent-as-a-pill-and-a-bubble", {
    visible: [seven, seven.getByText(retention, { exact: true }), north, north.getByText(region, { exact: true })],
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

test("Approve merge leaves Needs you at the press: not sent returns it with retry, a lost reply settles from the record", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const v = view(page, info);
  const release = v.article("Release notes");
  await walk.open("/");
  // Each send waits until the walk says how the connection ends: before Altitude saves it, or after.
  let end!: (how: "before" | "after") => void;
  await page.route("**/api/l2/message", async (route) => {
    const how = await new Promise<"before" | "after">((resolve) => { end = resolve; });
    if (how === "after") await route.fetch();
    await route.abort("connectionreset");
  });
  const approve = release.getByRole("button", { name: "Approve merge", exact: true });
  await walk.state("05-review-offers-approve-merge", {
    visible: [approve, release.getByRole("link", { name: "View PR #42", exact: true })], hidden: [release.getByRole("alert")],
  });
  let sent = page.waitForRequest((row) => new URL(row.url()).pathname === "/api/l2/message");
  await approve.click();
  await sent;
  await walk.state("06-approval-leaves-at-the-press", {
    visible: [page.getByRole("heading", { name: "Needs you", exact: true }), v.article("Index rollout")],
    hidden: [release, page.getByText("Approval saved", { exact: true })],
  });
  // Dropped before the save: the task's next read does not list it, so the card returns with Not sent.
  end("before");
  const retry = release.getByRole("button", { name: "Approve merge, retry", exact: true });
  await walk.state("07-not-sent-offers-retry", {
    visible: [retry, release.getByRole("alert").filter({ hasText: /^Not sent\.$/ }), release.getByRole("link", { name: "View PR #42", exact: true })],
    hidden: [page.getByText("Approval not sent", { exact: true })],
  });
  await expect(retry).toBeInViewport();
  const returned = await watch(page, 'article[aria-label="Release notes"]');
  sent = page.waitForRequest((row) => new URL(row.url()).pathname === "/api/l2/message");
  await retry.click();
  const body = (await sent).postDataJSON();
  expect(body.text).toBe("Approved: merge PR #42.");
  // Saved, then the reply was lost: the read lists the send id, so the approval settles without coming back.
  end("after");
  await walk.state("07b-approval-saved-without-its-reply", {
    visible: [page.getByText("Approval saved", { exact: true }), page.getByRole("button", { name: "Open L2 chat", exact: true })],
    hidden: [release],
  });
  expect(await returned(), "the card never returned").toBe(false);

  await page.unroute("**/api/l2/message");
  await walk.open("/projects/atlas/tasks/release-notes");
  const approval = v.pill("Approve merge", "PR #42");
  await expect(approval).toHaveAttribute("data-state", "sent");
  await walk.state("08-approval-in-the-chat", {
    visible: [approval, approval.getByText("PR #42", { exact: true })],
    hidden: [v.bubble("Approved: merge PR #42."), v.conversation.getByRole("button", { name: "Approve merge", exact: true })],
  });
  expect(await page.locator(".choice-mine").filter({ hasText: "PR #42" }).count(), "one approval, no duplicate").toBe(1);
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
