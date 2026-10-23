import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/*
 * The operator's message to the L2 carries its own state on the task page, as in project chat (SPEC.md §3.10,
 * §3.6): sending faded, kept while the operator leaves the task and returns, not sent with Retry and Edit,
 * unconfirmed with Copy to draft, refused for lost access, removing and not removed. Retry sends only where the
 * message went: a revised question or a new stop leaves Edit. The task fixture service stores every message and
 * makes its real refusals; transient refusals and lost answers are intercepted, and nothing reaches an engine.
 */

test.use({ serviceScript: "l2-progress-service.py" });

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
});

const BUSY = { status: 409, json: { error: "The task is busy. Try again in a moment." } };

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

/** One POST to the L2 waits until released, so the test can change the task while the message is on its way. */
async function holdSend(page: Page) {
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/l2/message", async (route) => { await held; await route.continue(); }, { times: 1 });
  return release;
}

async function setup(page: Page, request: APIRequestContext) {
  const slug = (await (await request.get("/fixture/status")).json()).tasks[0].slug as string;
  const convo = page.getByRole("region", { name: "Task conversation", exact: true });
  return {
    slug, convo,
    field: convo.getByRole("textbox", { name: "Message the L2" }),
    send: convo.getByRole("button", { name: "Send", exact: true }),
    row: (text: string) => convo.locator(".msg-row").filter({ hasText: text }),
    control: async (mode: string) => expect((await request.post("/fixture/control", { data: { slug, mode } })).ok()).toBe(true),
    steering: async () => (await (await request.get(`/api/task/atlas/${slug}`)).json()).steering.state as string,
  };
}

test("a message to the L2 stays on its bubble from Send until the record shows it, or says why not", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, convo, field, send, row } = await setup(page, request);
  await walk.open(`/projects/atlas/tasks/${slug}`);

  const release = await holdSend(page);
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
  // The server accepts it and the read that follows fails: an accepted send stays sent (PR #300).
  await page.route("**/api/task/atlas/*", (route) => route.fulfill({ status: 500, json: { error: "Could not read the task" } }), { times: 1 });
  release();
  await walk.state("03-settled-in-place", { visible: [row("Use the fixture data for the screenshots.").getByRole("button", { name: "Remove", exact: true })], hidden: [convo.getByText("Not sent:")] });
  await expect(row("Use the fixture data for the screenshots.")).not.toHaveAttribute("data-pending");
  await expect(convo.getByText("Use the fixture data for the screenshots.", { exact: true })).toHaveCount(1);

  await page.route("**/api/l2/message", (route) => route.fulfill(BUSY), { times: 1 });
  await field.fill("Ship the smaller option first.");
  await send.click();
  const refused = row("Ship the smaller option first.");
  await walk.state("04-not-sent-retry-edit", {
    visible: [refused.getByText("Not sent: the task is busy. Try again in a moment."), refused.getByRole("button", { name: "Retry" }), refused.getByRole("button", { name: "Edit" })],
    hidden: [convo.getByText("Not sent. ")],
  });
  await expect(field).toHaveValue("");
  await leaveAndReturn(page);
  await expect(refused.getByText("Not sent: the task is busy. Try again in a moment.")).toBeVisible();
  await settled(refused);
  await refused.getByRole("button", { name: "Retry" }).click();
  await walk.state("05-retry-sent-in-place", { visible: [refused.getByRole("button", { name: "Remove", exact: true })], hidden: [convo.getByText("Not sent:", { exact: false })] });
  await expect(convo.getByText("Ship the smaller option first.", { exact: true })).toHaveCount(1);

  await page.route("**/api/l2/message", (route) => route.fulfill(BUSY), { times: 1 });
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

  // The server stores the message and its answer is lost; reads wait, so the unknown outcome shows first.
  let reads!: () => void;
  const readsHeld = new Promise<void>((resolve) => { reads = resolve; });
  await page.route("**/api/task/atlas/*", async (route) => { await readsHeld; await route.continue(); });
  await page.route("**/api/l2/message", async (route) => { await route.fetch(); await route.abort("connectionreset"); }, { times: 1 });
  await field.fill("Check the release notes.");
  await send.click();
  const unconfirmed = row("Check the release notes.");
  await walk.state("07-unconfirmed-copy-to-draft", {
    visible: [unconfirmed.getByText("Unconfirmed"), unconfirmed.getByRole("button", { name: "Copy to draft" })],
    hidden: [unconfirmed.getByRole("button", { name: "Retry" })],
  });
  reads();
  await walk.state("08-unconfirmed-settles-from-the-record", { visible: [unconfirmed.getByRole("button", { name: "Remove", exact: true })], hidden: [unconfirmed.getByText("Unconfirmed")] });
  await expect(convo.getByText("Check the release notes.", { exact: true })).toHaveCount(1);
  await page.unroute("**/api/task/atlas/*");

  await page.route("**/api/l2/message", (route) => route.fulfill({ status: 403, json: { error: "Requests must come from Altitude's own page." } }), { times: 1 });
  await field.fill("Merge when green.");
  await send.click();
  const denied = row("Merge when green.");
  await walk.state("09-not-sent-access-lost", {
    visible: [denied.getByText("Not sent: requests must come from Altitude's own page."), convo.getByText("This page lost access.")],
    hidden: [denied.getByRole("button", { name: "Retry" }), denied.getByRole("button", { name: "Edit" })],
  });
  await convo.getByRole("button", { name: "Refresh", exact: true }).click();
  await walk.state("10-refresh-returns-retry-and-edit", {
    visible: [denied.getByRole("button", { name: "Retry" }), denied.getByRole("button", { name: "Edit" }), field],
    hidden: [convo.getByText("This page lost access.")],
  });
  await expect(field).toBeEnabled();
});

test("re-pairing starts the page afresh: unsent words return to the field as a draft, never sent by themselves", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, convo, field, send, row } = await setup(page, request);
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await page.route("**/api/l2/message", (route) => route.fulfill({ status: 401, json: { error: "Pair this device first.", pair: true } }), { times: 1 });
  await field.fill("Merge when green.");
  await send.click();
  await walk.state("11-pairing-asks-again", { visible: [page.getByText("This device is no longer paired. Pair it again to continue.")], hidden: [convo] });
  const code = (await (await request.post("/api/devices/code", { data: {} })).json()).code as string;
  await page.getByLabel("Pairing code").fill(code);
  await page.getByRole("button", { name: "Pair", exact: true }).click();
  // The words are a draft again: an ordinary Send sends them to what the page now shows.
  await walk.state("12-after-pairing-words-return-to-field", { visible: [field, convo.getByText("Not sent.", { exact: true })], hidden: [row("Merge when green."), convo.getByRole("button", { name: "Retry" })] });
  await expect(field).toHaveValue("Merge when green.");
  expect((await (await request.get(`/api/task/atlas/${slug}`)).json()).messages.some((message: { text: string }) => message.text === "Merge when green.")).toBe(false);
});

test("Retry sends only where the message went: a revised question or a new stop leaves Edit", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, convo, field, send, row, control, steering } = await setup(page, request);
  await walk.open(`/projects/atlas/tasks/${slug}`);

  // A refused message keeps Retry while the session is unchanged; Stop holds it while the session is stopping.
  await page.route("**/api/l2/message", (route) => route.fulfill(BUSY), { times: 1 });
  await field.fill("Keep the retry rule.");
  await send.click();
  const waiting = row("Keep the retry rule.");
  await expect(waiting.getByRole("button", { name: "Retry" })).toBeEnabled();
  await control("hold-stop");
  try {
    await page.getByRole("button", { name: "Stop", exact: true }).click();
    await walk.state("13-retry-waits-while-stopping", { visible: [page.getByRole("button", { name: "Stopping…", exact: true }), waiting.getByRole("button", { name: "Retry" })], hidden: [] });
    await expect(waiting.getByRole("button", { name: "Retry" })).toBeDisabled();
  } finally { await control("release-stop"); }
  await expect.poll(steering).toBe("stopped");
  // Sent to the running session; sending it now would continue the stopped one, so only Edit stays.
  await expect(waiting.getByRole("button", { name: "Retry" })).toBeHidden();
  await expect(waiting.getByRole("button", { name: "Edit" })).toBeVisible();

  // Another device continues and stops the session while this correction is on its way.
  const release = await holdSend(page);
  await field.fill("Use the cached page.");
  await send.click();
  await control("restop");
  release();
  const stale = row("Use the cached page.");
  await walk.state("14-stop-changed-edit-only", {
    visible: [stale.getByText("Not sent: the stopped session changed. Refresh before sending this correction."), stale.getByRole("button", { name: "Edit" })],
    hidden: [stale.getByRole("button", { name: "Retry" })],
  });
  await settled(stale);
  await stale.getByRole("button", { name: "Edit" }).click();
  await expect(field).toHaveValue("Use the cached page.");
});

test("a reply to a question group that is revised on its way leaves Edit, not Retry", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, convo, field, send, row, control } = await setup(page, request);
  await control("group");
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await expect(convo.getByText("Which page should load first?")).toBeVisible();
  const release = await holdSend(page);
  await field.fill("Keep twenty per page and load the first page.");
  await send.click();
  await control("revise-group");
  release();
  const stale = row("Keep twenty per page and load the first page.");
  await walk.state("15-question-changed-edit-only", {
    visible: [stale.getByText("Not sent: question group changed; refresh the conversation."), stale.getByRole("button", { name: "Edit" }), convo.getByText("Keep the original page size of 20?")],
    hidden: [stale.getByRole("button", { name: "Retry" })],
  });
  await settled(stale);
  await stale.getByRole("button", { name: "Edit" }).click();
  await expect(field).toHaveValue("Keep twenty per page and load the first page.");
  await send.click();
  await expect(row("Keep twenty per page and load the first page.").getByText("Not sent:")).toBeHidden();
  const stored = (await (await request.get(`/api/task/atlas/${slug}`)).json()).messages.filter((message: { text: string }) => message.text === "Keep twenty per page and load the first page.");
  expect(stored).toHaveLength(1);
  expect(stored[0].group_revision).toBe(2);
});

test("a refused image message keeps its images for Retry and Edit", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, convo, field, send, row } = await setup(page, request);
  const capability = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/images/atlas");
  await walk.open(`/projects/atlas/tasks/${slug}`);
  expect((await capability).ok()).toBe(true);
  const image = await page.evaluate(() => {
    const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 180;
    const context = canvas.getContext("2d")!;
    context.fillStyle = "#f8fafc"; context.fillRect(0, 0, 320, 180);
    context.fillStyle = "#172554"; context.font = "bold 22px sans-serif"; context.fillText("Page 2 of 9", 24, 48);
    return canvas.toDataURL("image/png").split(",")[1]!;
  });
  await page.route("**/api/l2/message", (route) => route.fulfill(BUSY), { times: 1 });
  await convo.getByLabel("Choose images").setInputFiles({ name: "pager.png", mimeType: "image/png", buffer: Buffer.from(image, "base64") });
  await expect(convo.getByRole("button", { name: "Remove image pager.png", exact: true })).toBeVisible();
  await field.fill("The pager loses the page here.");
  await send.click();
  const refused = row("The pager loses the page here.");
  await walk.state("16-image-not-sent-retry-edit", {
    visible: [refused.getByRole("img", { name: "pager.png" }), refused.getByRole("button", { name: "Retry" }), refused.getByRole("button", { name: "Edit" })],
    hidden: [convo.getByRole("button", { name: "Remove image pager.png", exact: true })],
  });
  await settled(refused);
  await refused.getByRole("button", { name: "Edit" }).click();
  await walk.state("17-image-edit-returns-to-picker", { visible: [convo.getByRole("button", { name: "Remove image pager.png", exact: true })], hidden: [refused] });
  await expect(field).toHaveValue("The pager loses the page here.");
});

test("Remove waits for the server: the × spins, and Not removed shows only once a read still lists the message", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, field, send, row: rowOf } = await setup(page, request);
  const row = rowOf("Hold the migration.");
  const remove = row.getByRole("button", { name: "Remove", exact: true });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await field.fill("Hold the migration.");
  await send.click();
  await expect(remove).toBeVisible();

  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/l2/remove", async (route) => { await held; await route.fulfill({ status: 500, json: { error: "Could not record the removal" } }); }, { times: 1 });
  await walk.state("18-removing", { action: () => remove.click(), visible: [row.getByRole("button", { name: "Removing" })], hidden: [] });
  release();
  await walk.state("19-not-removed-retry", {
    visible: [row.getByText("Not removed: could not record the removal."), row.getByRole("button", { name: "Retry" })],
    hidden: [],
  });
  await row.getByRole("button", { name: "Retry" }).click();
  await walk.state("20-removed", { visible: [field], hidden: [row] });
});

test("rows that arrive while the task is open enter with a short fade; rows already there do not", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, convo, field, send, row } = await setup(page, request);
  await walk.open(`/projects/atlas/tasks/${slug}`);
  const release = await holdSend(page);
  await field.fill("First note.");
  await send.click();
  const first = row("First note.");
  await walk.state("21-new-message-enters", { visible: [first], hidden: [] });
  await expect(first).toHaveAttribute("data-enter", "true");
  release();
  // Settling in place is no second entrance.
  await expect(first.getByRole("button", { name: "Remove", exact: true })).toBeVisible();
  await expect(first).not.toHaveAttribute("data-enter");
  // A message that arrives from elsewhere while the task is open enters on the next read.
  expect((await request.post("/api/l2/message", { data: { project: "atlas", slug, text: "Arrived from another device." } })).ok()).toBe(true);
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  const arrived = row("Arrived from another device.");
  await walk.state("22-arrival-enters", { visible: [arrived], hidden: [] });
  await expect(arrived).toHaveAttribute("data-enter", "true");
  await page.reload();
  await expect(arrived).toBeVisible();
  await expect(convo.locator("[data-enter]")).toHaveCount(0);
});

test("a refused message keeps its line and Retry after the task finishes, where sending reopens it", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { slug, convo, field, send, row, control } = await setup(page, request);
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await page.route("**/api/l2/message", (route) => route.fulfill(BUSY), { times: 1 });
  await field.fill("Add one more check.");
  await send.click();
  const refused = row("Add one more check.");
  await expect(refused.getByRole("button", { name: "Retry" })).toBeVisible();
  await control("finished");
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await walk.state("23-finished-keeps-failure-and-retry", {
    visible: [refused.getByText("Not sent: the task is busy. Try again in a moment."), convo.getByText("Pagination and steering are verified."), field,
      page.getByText("Sending reopens the task with your message.", { exact: true }), refused.getByRole("button", { name: "Retry" })],
    hidden: [refused.getByRole("button", { name: "Copy to draft" })],
  });
});
