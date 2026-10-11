import { expect, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/*
 * The operator's message to the L2 carries its own state on the task page, as in project chat (SPEC.md §3.10,
 * §3.6): sending faded, kept while the operator leaves the task and returns, not sent with Retry and Edit,
 * unconfirmed with Copy to draft, refused for lost access, removing and not removed. The task fixture service
 * stores every message; refusals and lost answers are intercepted, and nothing reaches an engine.
 */

test.use({ serviceScript: "l2-progress-service.py" });

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
});

/**
 * A row's entrance has finished. Playwright retries a click on a moving target with other scroll alignments,
 * which slide the phone's side-by-side views; a person's tap never does.
 */
async function settled(row: Locator) {
  await row.evaluate((node) => Promise.all(node.getAnimations({ subtree: true }).map((animation) => animation.finished)));
}

/** Leave the task within this document, as a tab or link would, and come back to it. */
async function leaveAndReturn(page: Page) {
  const task = new URL(page.url()).pathname;
  await page.evaluate(() => { history.pushState({}, "", "/projects/atlas"); dispatchEvent(new PopStateEvent("popstate")); });
  await expect(page.getByRole("region", { name: "Task conversation", exact: true })).toBeHidden();
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`${task}$`));
}

test("a message to the L2 stays on its bubble from Send until the record shows it, or says why not", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = (await (await request.get("/fixture/status")).json()).tasks[0].slug;
  const convo = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = convo.getByRole("textbox", { name: "Message the L2" });
  const send = convo.getByRole("button", { name: "Send", exact: true });
  const row = (text: string) => convo.locator(".msg-row").filter({ hasText: text });
  await walk.open(`/projects/atlas/tasks/${slug}`);

  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/l2/message", async (route) => { await held; await route.continue(); }, { times: 1 });
  await field.fill("Use the fixture data for the screenshots.");
  await walk.state("01-sending-faded", {
    action: () => send.click(),
    visible: [row("Use the fixture data for the screenshots.")],
    hidden: [row("Use the fixture data for the screenshots.").locator(".spinner")],
  });
  await expect(row("Use the fixture data for the screenshots.")).toHaveAttribute("data-pending", "true");
  await expect(field).toHaveValue("");
  await leaveAndReturn(page);
  await walk.state("02-still-sending-after-leaving", { visible: [row("Use the fixture data for the screenshots.")], hidden: [] });
  await expect(row("Use the fixture data for the screenshots.")).toHaveAttribute("data-pending", "true");
  release();
  await walk.state("03-settled-in-place", { visible: [row("Use the fixture data for the screenshots.").getByRole("button", { name: "Remove", exact: true })], hidden: [] });
  await expect(row("Use the fixture data for the screenshots.")).not.toHaveAttribute("data-pending");
  await expect(convo.getByText("Use the fixture data for the screenshots.", { exact: true })).toHaveCount(1);

  await page.route("**/api/l2/message", (route) => route.fulfill({ status: 409, json: { error: "question group changed; refresh the conversation" } }), { times: 1 });
  await field.fill("Ship the smaller option first.");
  await send.click();
  const refused = row("Ship the smaller option first.");
  await walk.state("04-not-sent-retry-edit", {
    visible: [refused.getByText("Not sent: question group changed; refresh the conversation."), refused.getByRole("button", { name: "Retry" }), refused.getByRole("button", { name: "Edit" })],
    hidden: [convo.getByText("Not sent. ")],
  });
  await expect(field).toHaveValue("");
  await leaveAndReturn(page);
  await expect(refused.getByText("Not sent: question group changed; refresh the conversation.")).toBeVisible();
  await settled(refused);
  await refused.getByRole("button", { name: "Retry" }).click();
  await walk.state("05-retry-sent-in-place", { visible: [refused.getByRole("button", { name: "Remove", exact: true })], hidden: [convo.getByText("Not sent:", { exact: false })] });
  await expect(convo.getByText("Ship the smaller option first.", { exact: true })).toHaveCount(1);

  await page.route("**/api/l2/message", (route) => route.fulfill({ status: 409, json: { error: "The stopped session changed. Refresh before sending this correction." } }), { times: 1 });
  await field.fill("Keep the old wording.");
  await send.click();
  await field.fill("Draft in progress");
  await settled(row("Keep the old wording."));
  await row("Keep the old wording.").getByRole("button", { name: "Edit" }).click();
  // The words return without sliding the phone's views: the conversation stays where it was.
  await expect(field).toBeInViewport({ ratio: 1 });
  await walk.state("06-edit-returns-words-ahead-of-draft", { visible: [field], hidden: [row("Keep the old wording.")] });
  await expect(field).toHaveValue("Keep the old wording.\nDraft in progress");
  await field.fill("");

  await page.route("**/api/l2/message", (route) => route.abort("connectionreset"), { times: 1 });
  await field.fill("Check the release notes.");
  await send.click();
  const unconfirmed = row("Check the release notes.");
  await walk.state("07-unconfirmed-copy-to-draft", {
    visible: [unconfirmed.getByText("Unconfirmed"), unconfirmed.getByRole("button", { name: "Copy to draft" })],
    hidden: [unconfirmed.getByRole("button", { name: "Retry" })],
  });

  await page.route("**/api/l2/message", (route) => route.fulfill({ status: 403, json: { error: "Requests must come from Altitude's own page." } }), { times: 1 });
  await field.fill("Merge when green.");
  await send.click();
  const denied = row("Merge when green.");
  await walk.state("08-not-sent-access-lost", {
    visible: [denied.getByText("Not sent: requests must come from Altitude's own page."), convo.getByText("You cannot send messages or answers here.")],
    hidden: [denied.getByRole("button", { name: "Retry" }), denied.getByRole("button", { name: "Edit" })],
  });
});

test("Remove waits for the server: the × spins, and Not removed shows only once a read still lists the message", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = (await (await request.get("/fixture/status")).json()).tasks[0].slug;
  const convo = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = convo.getByRole("textbox", { name: "Message the L2" });
  const row = convo.locator(".msg-row").filter({ hasText: "Hold the migration." });
  const remove = row.getByRole("button", { name: "Remove", exact: true });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await field.fill("Hold the migration.");
  await convo.getByRole("button", { name: "Send", exact: true }).click();
  await expect(remove).toBeVisible();

  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/l2/remove", async (route) => { await held; await route.fulfill({ status: 500, json: { error: "Could not record the removal" } }); }, { times: 1 });
  await walk.state("09-removing", { action: () => remove.click(), visible: [row.getByRole("button", { name: "Removing" })], hidden: [] });
  release();
  await walk.state("10-not-removed-retry", {
    visible: [row.getByText("Not removed: could not record the removal."), row.getByRole("button", { name: "Retry" })],
    hidden: [],
  });
  await row.getByRole("button", { name: "Retry" }).click();
  await walk.state("11-removed", { visible: [field], hidden: [row] });
});

test("rows that arrive while the task is open enter with a short fade; rows already there do not", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = (await (await request.get("/fixture/status")).json()).tasks[0].slug;
  const convo = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = convo.getByRole("textbox", { name: "Message the L2" });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/l2/message", async (route) => { await held; await route.continue(); }, { times: 1 });
  await field.fill("First note.");
  await convo.getByRole("button", { name: "Send", exact: true }).click();
  const first = convo.locator(".msg-row").filter({ hasText: "First note." });
  await walk.state("12-new-message-enters", { visible: [first], hidden: [] });
  await expect(first).toHaveAttribute("data-enter", "true");
  release();
  // Settling in place is no second entrance.
  await expect(first.getByRole("button", { name: "Remove", exact: true })).toBeVisible();
  await expect(first).not.toHaveAttribute("data-enter");
  // A message that arrives from elsewhere while the task is open enters on the next read.
  expect((await request.post("/api/l2/message", { data: { project: "atlas", slug, text: "Arrived from another device." } })).ok()).toBe(true);
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  const arrived = convo.locator(".msg-row").filter({ hasText: "Arrived from another device." });
  await walk.state("13-arrival-enters", { visible: [arrived], hidden: [] });
  await expect(arrived).toHaveAttribute("data-enter", "true");
  await page.reload();
  await expect(arrived).toBeVisible();
  await expect(convo.locator("[data-enter]")).toHaveCount(0);
});
