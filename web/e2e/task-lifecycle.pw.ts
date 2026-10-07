import { expect, type Route } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureHost } from "./hostVoice";
import { walkthrough } from "./walkthrough";

test.use({ scenario: "tasks" });

test("voice Send finishes in its original L2 conversation while viewing L3", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "prepare-index-migration";
  const task = async () => (await (await request.get(`/api/task/atlas/${slug}`)).json());
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  // Host voice: the page's real audio worklet hears the fake microphone; the fixture host holds the final words.
  const host = await fixtureHost(page);
  host.final = "Include the failure reason.";
  let release!: () => void;
  host.holdFinal = new Promise<void>((resolve) => { release = resolve; });
  const posts: { url: string; project: string; slug?: string; text: string }[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url()).pathname;
    if (["/api/chat", "/api/l2/message"].includes(url) && request.method() === "POST") posts.push({ url, ...request.postDataJSON() });
  });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await field.fill("Keep retry bounded.");
  await conversation.getByRole("button", { name: "Start voice input" }).click();
  await expect(conversation.getByRole("button", { name: "Stop voice input" })).toBeVisible();
  await expect(field).toHaveValue("Keep retry bounded. check the build", { timeout: 5000 });
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => host.finals).toBe(1);
  const nav = page.getByRole("navigation", { name: info.project.name === "phone" ? "Primary" : "Rail", exact: true });
  await nav.locator('a[href="/projects/atlas"]').click();
  const projectField = page.getByRole("textbox", { name: "Message L3 about atlas", exact: true });
  await projectField.fill("Unsent project draft.");
  await walk.state("01-project-draft-while-l2-transcribes", { visible: [projectField], hidden: [conversation, page.getByText("Transcribing…", { exact: true })] });
  release();
  const finalText = "Keep retry bounded. Include the failure reason.";
  await expect.poll(async () => (await task()).messages.filter((row: { text: string }) => row.text === finalText).length).toBe(1);
  await expect(projectField).toHaveValue("Unsent project draft.");
  const project = await (await request.get("/api/chat/atlas")).json();
  expect([...project.history, ...(project.queued ?? [])].some((row: { text: string }) => row.text === finalText)).toBe(false);
  if (info.project.name === "phone") await nav.getByRole("link", { name: "Work", exact: true }).click();
  await page.locator(`a[href="/projects/atlas/tasks/${slug}"]`).first().click();
  await walk.state("02-l2-message-delivered-once", { visible: [conversation.getByText(finalText, { exact: true }), field], hidden: [page.getByText("Transcribing…", { exact: true })] });
  await expect(field).toHaveValue("");
  expect(host.finals).toBe(1);
  expect(host.opened).toBe(1);
  expect(posts).toHaveLength(1);
  expect(posts[0]).toMatchObject({ url: "/api/l2/message", project: "atlas", slug, text: finalText });
  const workers = await (await request.get("/fixture/workers")).json();
  expect(workers.pending[slug].filter((row: { text: string }) => row.text === finalText)).toHaveLength(1);
  expect(workers.calls).toEqual([]);
});

// No HTTP overlays: real browser -> Handler -> task/inbox/daemon request -> Git provenance -> fake engine.
test("L2 messaging resumes its saved session, queues later input, stops and archives with a durable reason", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "clarify-retry-policy";
  const path = `/projects/atlas/tasks/${slug}`;
  const task = async () => (await (await request.get(`/api/task/atlas/${slug}`)).json());
  const workers = async () => (await (await request.get("/fixture/workers")).json());
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const reject = page.locator(".task-action").filter({ hasText: /^Reject$/ });
  await walk.open(path);
  await walk.state("01-blocked-composer", { visible: [field, conversation], hidden: [] });
  await field.fill("Keep retry attempts bounded to two.");
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(async () => (await task()).state).toBe("running");
  const resumed = await task();
  expect(resumed.session_id).toBe(`fixture-${slug}`);
  expect(resumed.resume_claim).toBeFalsy();
  expect(resumed.messages.filter((row: { text: string }) => row.text === "Keep retry attempts bounded to two.")).toHaveLength(1);
  const first = await workers();
  expect(first.calls).toHaveLength(1);
  expect(first.calls[0].session_id).toBe(`fixture-${slug}`);
  expect(first.calls[0].prompt).toContain("Keep retry attempts bounded to two.");
  expect(first.pending[slug]).toEqual([]);
  await page.reload();
  await walk.state("02-resumed-message-durable", {
    visible: [conversation.getByText("Keep retry attempts bounded to two.", { exact: true }), field], hidden: [],
  });
  await field.fill("Include the failure reason in the result.");
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(async () => (await workers()).pending[slug].map((row: { text: string }) => row.text))
    .toEqual(["Include the failure reason in the result."]);
  expect((await workers()).calls).toHaveLength(1);
  expect((await task()).agent_id).toBe(resumed.agent_id);
  await walk.state("03-running-input-queued", {
    visible: [conversation.getByText("Include the failure reason in the result.", { exact: true })], hidden: [],
  });
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect.poll(async () => (await task()).daemon_request?.status).toBe("done");
  expect((await task()).state).toBe("blocked");
  expect((await workers()).workers[resumed.agent_id].state).toBe("stopped");
  await page.reload();
  if (info.project.name === "phone") await page.getByRole("button", { name: /Task details$/ }).click();
  await walk.state("04-stopped", { visible: [field, reject], hidden: [] });
  await reject.click();
  const confirm = page.getByRole("group", { name: "Reject this task?", exact: true });
  await expect(confirm.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
  await confirm.getByRole("textbox", { name: "Reason (optional)", exact: true }).fill("The fixture exercise is complete.");
  await confirm.getByRole("button", { name: "Reject task", exact: true }).click();
  await expect.poll(async () => (await task()).state).toBe("rejected");
  await page.reload();
  await walk.state("05-rejected-read-only", {
    visible: [page.getByText("Rejected", { exact: true }).first()],
    hidden: [field, page.getByRole("button", { name: "Stop", exact: true }), page.getByRole("button", { name: "Reject", exact: true })],
  });
  const archived = await task();
  expect(archived.daemon_request.status).toBe("done");
  expect(archived.daemon_request.reason).toBe("The fixture exercise is complete.");
  const project = await (await request.get("/api/project/atlas")).json();
  expect(project.tasks.some((row: { slug: string }) => row.slug === slug)).toBe(false);
  expect(project.archive.some((row: { slug: string }) => row.slug === slug)).toBe(true);
  expect((await request.post("/api/l2/message", { data: { project: "atlas", slug, text: "Too late" } })).status()).toBe(409);
  expect((await task()).messages.some((row: { text: string }) => row.text === "Too late")).toBe(false);
});

test("accepted L2 input stays sent through a failed refresh and preserves the next draft", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "prepare-index-migration";
  const task = async () => (await (await request.get(`/api/task/atlas/${slug}`)).json());
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const text = "Keep this accepted instruction in the running inbox.";
  const alert = conversation.getByRole("alert").filter({ hasText: "Not sent." });
  const retry = conversation.getByRole("button", { name: "Retry", exact: true });
  const pending = conversation.locator(".msg-row[data-pending]");
  const bubble = conversation.locator(".bubble").filter({ hasText: text });
  let admit!: () => void;
  const admissionGate = new Promise<void>((resolve) => { admit = resolve; });
  let release!: () => void;
  const responseGate = new Promise<void>((resolve) => { release = resolve; });
  let saved!: () => void;
  const savedGate = new Promise<void>((resolve) => { saved = resolve; });
  let receipt!: { id: string };
  // Gates expose the preview before admission and polling before the real handler's response.
  await page.route("**/api/l2/message", async (route) => {
    await admissionGate;
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    receipt = (await response.json()).message;
    saved();
    await responseGate;
    await route.fulfill({ response });
  }, { times: 1 });
  let offline = false;
  let failedRead!: () => void;
  const refreshFailed = new Promise<void>((resolve) => { failedRead = resolve; });
  await page.route(`**/api/task/atlas/${slug}`, async (route) => {
    if (!offline) return route.continue();
    await route.abort("connectionfailed");
    failedRead();
  });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await field.fill(text);
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  try {
    await expect(field).toHaveValue("");
    await walk.state("01-message-pending-before-save", { visible: [bubble, pending], hidden: [alert, retry] });
    admit();
    await savedGate;
    // A real poll renders the saved row's Remove control while the POST is still held.
    await expect(conversation.locator(".msg-row").filter({ hasText: text }).getByRole("button", { name: "Remove", exact: true })).toBeVisible();
    await expect(bubble).toHaveCount(1);
    await walk.state("02-message-polled-response-pending", { visible: [bubble], hidden: [pending, alert, retry] });
    expect((await task()).messages.filter((row: { id: string }) => row.id === receipt.id)).toHaveLength(1);
    await field.fill("A different instruction I have not sent.");
    offline = true;
  } finally { admit(); release(); }
  await refreshFailed;
  await walk.state("03-accepted-refresh-failed-next-draft-retained", { visible: [bubble, field], hidden: [pending, alert, retry] });
  await expect(field).toHaveValue("A different instruction I have not sent.");
  const workers = await (await request.get("/fixture/workers")).json();
  expect(workers.pending[slug].filter((row: { id: string }) => row.id === receipt.id)).toHaveLength(1);
  expect(workers.calls).toHaveLength(0);
  offline = false;
  await page.reload();
  await walk.state("04-reconnected-message-still-sent-once", { visible: [bubble, field], hidden: [pending, alert, retry] });
  await expect(bubble).toHaveCount(1);
  await expect(field).toHaveValue("");
  expect((await task()).messages.filter((row: { id: string }) => row.id === receipt.id)).toHaveLength(1);
});

test("late L2 receipt preserves polled removal, message order and the next draft across Live switches", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "prepare-index-migration";
  const text = "Withdraw this instruction before the next checkpoint.";
  const later = "Keep this later instruction in its original position.";
  const draft = "An unsent draft after both instructions.";
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const removed = conversation.getByText("Removed · not sent to the session", { exact: true });
  const bubbles = conversation.locator(".bubble").filter({ hasText: /Message removed|Withdraw this instruction|Keep this later instruction/ });
  let releaseReceipt!: () => void;
  const receiptGate = new Promise<void>((resolve) => { releaseReceipt = resolve; });
  let saved!: () => void;
  const savedGate = new Promise<void>((resolve) => { saved = resolve; });
  let receipt!: { id: string; delivery: { state: string } };
  let submissions = 0;
  await page.route("**/api/l2/message", async (route) => {
    submissions++;
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    receipt = (await response.json()).message;
    saved();
    await receiptGate;
    await route.fulfill({ response });
  });
  let holdReads = false;
  let releaseReads!: () => void;
  const readGate = new Promise<void>((resolve) => { releaseReads = resolve; });
  let readHeld!: () => void;
  const heldRead = new Promise<void>((resolve) => { readHeld = resolve; });
  await page.route(`**/api/task/atlas/${slug}`, async (route) => {
    if (holdReads) { readHeld(); await readGate; }
    await route.continue();
  });
  try {
    await walk.open(`/projects/atlas/tasks/${slug}`);
    await field.fill(text);
    await conversation.getByRole("button", { name: "Send", exact: true }).click();
    await savedGate;
    expect(receipt.delivery.state).toBe("queued");
    // Removal and the later send use the real API/storage while the browser still awaits its receipt.
    expect((await request.post("/api/l2/remove", { data: { project: "atlas", slug, id: receipt.id } })).ok()).toBe(true);
    expect((await request.post("/api/l2/message", { data: { project: "atlas", slug, text: later } })).ok()).toBe(true);
    await expect(bubbles).toHaveText(["Message removed", later]);
    await expect(removed).toBeVisible();
    await field.fill(draft);
    for (let index = 0; index < 2; index++) {
      if (info.project.name === "phone") {
        const tabs = page.getByRole("navigation", { name: "Task views" });
        await tabs.getByRole("link", { name: "Live session", exact: true }).click();
        await tabs.getByRole("link", { name: "Conversation", exact: true }).click();
      } else {
        const toggle = page.getByRole("button", { name: "Live session", exact: true });
        await toggle.click();
        await toggle.click();
      }
    }
    await walk.state("01-canonical-removal-before-late-receipt", { visible: [removed, field], hidden: [conversation.getByText(text, { exact: true })] });
    await expect(field).toHaveValue(draft);
    // Keep the receipt's subsequent refresh pending so it cannot hide a stale cache mutation.
    holdReads = true;
    const response = page.waitForResponse((response) => response.url().endsWith("/api/l2/message") && response.request().method() === "POST");
    releaseReceipt();
    await response;
    await heldRead;
    await expect(bubbles).toHaveText(["Message removed", later]);
    await expect(field).toHaveValue(draft);
    await walk.state("02-late-receipt-keeps-removal-order-and-draft", { visible: [removed, field], hidden: [conversation.getByText(text, { exact: true }), conversation.locator(".msg-row[data-pending]")] });
    expect(submissions).toBe(1);
    const task = await (await request.get(`/api/task/atlas/${slug}`)).json();
    expect(task.messages.filter((row: { id: string }) => row.id === receipt.id)).toHaveLength(1);
    expect(task.messages.find((row: { id: string }) => row.id === receipt.id).delivery.state).toBe("removed");
    const workers = await (await request.get("/fixture/workers")).json();
    expect(workers.pending[slug].map((row: { text: string }) => row.text)).toEqual([later]);
    expect(workers.calls).toHaveLength(0);
  } finally { releaseReceipt(); releaseReads(); }
});

for (const lostReceipt of [false, true]) test(`L2 immediate Live and task navigation retains ${lostReceipt ? "unconfirmed recovery" : "accepted input"} through reload`, async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "prepare-index-migration";
  const other = "clarify-retry-policy";
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const text = "Retain this fictional navigation instruction.";
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let arrived!: () => void;
  const waiting = new Promise<void>((resolve) => { arrived = resolve; });
  let finished!: () => void;
  const delivered = new Promise<void>((resolve) => { finished = resolve; });
  let receipt!: { id: string };
  let submissions = 0;
  await page.route("**/api/l2/message", async (route) => {
    submissions++;
    arrived();
    await gate; // Navigation happens before the real handler accepts the message.
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    receipt = (await response.json()).message;
    if (lostReceipt) await route.abort("connectionfailed");
    else await route.fulfill({ response });
    finished();
  });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await field.fill(text);
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  await waiting;
  if (info.project.name === "phone") {
    await page.getByRole("navigation", { name: "Task views" }).getByRole("link", { name: "Live session", exact: true }).click();
  } else {
    const toggle = page.getByRole("button", { name: "Live session", exact: true });
    if (await toggle.getAttribute("aria-pressed") !== "true") await toggle.click();
  }
  await walk.state("01-live-before-admission", { visible: [page.getByRole("region", { name: "Live session", exact: true })], hidden: info.project.name === "phone" ? [conversation] : [] });
  const nav = page.getByRole("navigation", { name: info.project.name === "phone" ? "Primary" : "Rail", exact: true });
  await nav.locator('a[href="/projects/atlas"]').click();
  if (info.project.name === "phone") await nav.getByRole("link", { name: "Work", exact: true }).click();
  await page.locator(`a[href="/projects/atlas/tasks/${other}"]`).first().click();
  await field.fill("Other task draft remains local.");
  release();
  await delivered;
  await expect(field).toHaveValue("Other task draft remains local.");
  await walk.state("02-other-task-after-late-response", { visible: [field], hidden: [conversation.getByRole("alert"), conversation.getByText(text, { exact: true })] });
  const original = await (await request.get(`/api/task/atlas/${slug}`)).json();
  expect(original.messages.filter((row: { id: string }) => row.id === receipt.id)).toHaveLength(1);
  const destination = await (await request.get(`/api/task/atlas/${other}`)).json();
  expect(destination.messages.some((row: { text: string }) => row.text === text)).toBe(false);
  const workers = await (await request.get("/fixture/workers")).json();
  expect(workers.pending[slug].filter((row: { id: string }) => row.id === receipt.id)).toHaveLength(1);
  expect(workers.calls).toHaveLength(0);
  await nav.locator('a[href="/projects/atlas"]').click();
  if (info.project.name === "phone") await nav.getByRole("link", { name: "Work", exact: true }).click();
  await page.locator(`a[href="/projects/atlas/tasks/${slug}"]`).first().click();
  await expect(page).toHaveURL(`/projects/atlas/tasks/${slug}`);
  const bubble = conversation.locator(".bubble").filter({ hasText: text });
  const hint = conversation.getByRole("alert").filter({ hasText: "Could not confirm delivery." });
  const retry = conversation.getByRole("button", { name: "Retry", exact: true });
  await expect(field).toHaveValue(lostReceipt ? text : "");
  await walk.state("03-returned-original-conversation", { visible: [bubble, ...(lostReceipt ? [hint] : [])], hidden: [retry, ...(!lostReceipt ? [hint] : [])] });
  await page.reload();
  await expect(field).toHaveValue(lostReceipt ? text : "");
  await walk.state("04-reloaded-original-conversation", { visible: [bubble, ...(lostReceipt ? [hint] : [])], hidden: [retry, ...(!lostReceipt ? [hint] : [])] });
  await expect(bubble).toHaveCount(1);
  expect(submissions).toBe(1);
  expect((await (await request.get(`/api/task/atlas/${slug}`)).json()).messages.filter((row: { id: string }) => row.id === receipt.id)).toHaveLength(1);
});

for (const refusalFirst of [true, false]) test(`overlapping L2 refusal and lost receipt preserve all drafts without Retry; refusal first: ${refusalFirst}`, async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "prepare-index-migration";
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const routes: Route[] = [];
  await page.route("**/api/l2/message", (route) => { routes.push(route); });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  for (const text of ["Refused instruction", "Stored instruction"]) {
    await field.fill(text);
    await conversation.getByRole("button", { name: "Send", exact: true }).click();
  }
  await expect.poll(() => routes.length).toBe(2);
  await field.fill("Next draft");
  // Only the second request reaches the real handler; its saved receipt is lost in transport.
  const response = await routes[1]!.fetch();
  expect(response.ok()).toBe(true);
  const { message } = await response.json();
  let recovered = "Next draft";
  for (const index of refusalFirst ? [0, 1] : [1, 0]) {
    if (index === 0) await routes[0]!.fulfill({ status: 409, json: { error: "Refused before storage" } });
    else await routes[1]!.abort("connectionfailed");
    recovered = `${index === 0 ? "Refused instruction" : "Stored instruction"}\n${recovered}`;
    await expect(field).toHaveValue(recovered);
  }
  await walk.state("mixed-delivery-uncertain-drafts-preserved", {
    visible: [conversation.getByRole("alert").filter({ hasText: "Could not confirm delivery." }), field],
    hidden: [conversation.getByRole("button", { name: "Retry", exact: true })],
  });
  const task = await (await request.get(`/api/task/atlas/${slug}`)).json();
  expect(task.messages.filter((row: { id: string }) => row.id === message.id)).toHaveLength(1);
  expect(task.messages.some((row: { text: string }) => row.text === "Refused instruction")).toBe(false);
  const workers = await (await request.get("/fixture/workers")).json();
  expect(workers.pending[slug].filter((row: { id: string }) => row.id === message.id)).toHaveLength(1);
  expect(routes).toHaveLength(2);
});

test("sending a quick answer persists its context, resumes the same L2 and clears Needs you", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "choose-validation-scope";
  const task = async () => (await (await request.get(`/api/task/atlas/${slug}`)).json());
  await walk.open("/");
  const card = page.getByRole("article", { name: "Choose validation scope", exact: true });
  await walk.state("01-needs-you", { visible: [card], hidden: [] });
  await card.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  const question = (await task()).question;
  const inline = page.getByRole("region", { name: "Task conversation", exact: true }).locator(`[data-question-id="${question.id}"][data-question-revision="${question.revision}"]`);
  const choice = inline.getByRole("button", { name: "Keep the bounded scope", exact: true });
  await walk.state("02-recorded-question", { visible: [choice, page.getByRole("textbox", { name: "Message the L2", exact: true })], hidden: [page.getByPlaceholder("Add a note for the L2 (optional)")] });
  await choice.click();
  await page.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect.poll(async () => (await task()).state).toBe("running");
  const resumed = await task();
  expect(resumed.state).toBe("running");
  const submitted = resumed.questions.find((row: { id: string; revision: number }) => row.id === question.id && row.revision === question.revision);
  expect(submitted.status).toBe("open");
  expect(submitted.resolution).toBeNull();
  expect(submitted.response).toMatchObject({ text: "Keep the bounded scope." });
  expect(resumed.session_id).toBe(`fixture-${slug}`);
  expect(resumed.messages.filter((row: { id: string }) => row.id === submitted.response.message_id)).toHaveLength(1);
  const workers = await (await request.get("/fixture/workers")).json();
  expect(workers.calls).toHaveLength(1);
  expect(workers.calls[0].prompt).toContain("Keep the bounded scope.");
  await page.reload();
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  await walk.state("03-answer-durable-awaiting-interpretation", {
    visible: [conversation.getByRole("status").filter({ hasText: "Sent · the L2 has your reply." }), conversation.locator(".bubble").filter({ hasText: "Keep the bounded scope." })],
    hidden: [inline, choice, conversation.getByText("Decision recorded", { exact: true })],
  });
  await walk.open("/");
  await walk.state("04-needs-you-cleared", { visible: [page.getByRole("heading", { name: "Needs you", exact: true })], hidden: [card] });
  const overview = await (await request.get("/api/overview")).json();
  expect(overview.queue.some((row: { slug: string }) => row.slug === slug)).toBe(false);
});

test("an operational stop resumes the saved L2 without inventing a decision; a failed request stays retryable", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "prepare-index-migration";
  const task = async () => (await (await request.get(`/api/task/atlas/${slug}`)).json());
  const workers = async () => (await (await request.get("/fixture/workers")).json());
  const initial = await task();
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect.poll(async () => (await task()).daemon_request?.status).toBe("done");
  expect((await task()).state).toBe("blocked");
  expect((await task()).question).toBeNull();
  await page.reload();
  const resume = page.getByRole("button", { name: "Continue", exact: true });
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  await walk.state("01-stopped-with-operational-resume", {
    visible: [resume, page.getByRole("textbox", { name: "Message the L2", exact: true })],
    hidden: [conversation.locator("[data-question-id]"), page.getByRole("button", { name: "Stop", exact: true })],
  });
  const stateLine = page.locator(".task-state-line");
  const stateLineHeight = info.project.name === "phone" ? (await stateLine.boundingBox())!.height : 0;
  await page.route("**/api/task/action", (route) => route.fulfill({ status: 503, json: { error: "The resume request could not be saved." } }), { times: 1 });
  await resume.click();
  const error = page.getByRole("alert").filter({ hasText: "Could not confirm continuation." });
  const checkStatus = page.getByRole("button", { name: "Check status", exact: true });
  await walk.state("02-resume-request-failed", { visible: [error, checkStatus], hidden: [resume, page.getByRole("button", { name: "Resuming…", exact: true })] });
  if (info.project.name === "phone") {
    await expect.poll(async () => (await stateLine.boundingBox())!.height, {
      message: "The resume error must not squeeze the readable task status into a vertical stack",
    }).toBeLessThanOrEqual(stateLineHeight + 1);
  }
  expect((await task()).state).toBe("blocked");
  expect((await workers()).calls).toHaveLength(0);
  await checkStatus.click();
  await expect(resume).toBeVisible();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/task/action", async (route) => { await gate; await route.continue(); }, { times: 1 });
  try {
    await resume.click();
    const pending = page.getByRole("button", { name: "Resuming…", exact: true });
    await expect(resume).toBeHidden();
    await walk.state("03-resume-request-pending", { visible: [pending], hidden: [error] });
  } finally { release(); }
  await expect.poll(async () => (await task()).state).toBe("running");
  const resumed = await task();
  expect(resumed.session_id).toBe(initial.session_id);
  expect(resumed.attempt).toBe(initial.attempt);
  expect(resumed.question).toBeNull();
  const activity = await workers();
  expect(activity.calls).toHaveLength(1);
  expect(activity.calls[0].session_id).toBe(initial.session_id);
  const overview = await (await request.get("/api/overview")).json();
  expect(overview.queue.some((row: { slug: string }) => row.slug === slug)).toBe(false);
  await page.reload();
  await walk.state("04-saved-session-resumed", {
    visible: [page.getByRole("button", { name: "Stop", exact: true }), page.getByRole("textbox", { name: "Message the L2", exact: true })],
    hidden: [resume, conversation.locator("[data-question-id]"), conversation.getByText("Decision recorded", { exact: true })],
  });
});
