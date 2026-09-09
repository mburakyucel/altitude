import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ scenario: "tasks" });

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
  if (info.project.name === "phone") await page.getByRole("button", { name: "Task details", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page.getByRole("group", { name: "Stop this task?", exact: true }).getByRole("button", { name: "Stop", exact: true }).click();
  await expect.poll(async () => (await task()).daemon_request?.status).toBe("done");
  expect((await task()).state).toBe("blocked");
  expect((await workers()).workers[resumed.agent_id].state).toBe("stopped");
  await page.reload();
  if (info.project.name === "phone") await page.getByRole("button", { name: "Task details", exact: true }).click();
  await walk.state("04-stopped", { visible: [field, reject], hidden: [] });
  await reject.click();
  const confirm = page.getByRole("group", { name: "Reject this task?", exact: true });
  await confirm.getByRole("textbox", { name: "Reason (optional)", exact: true }).fill("The fixture exercise is complete.");
  await confirm.getByRole("button", { name: "Reject", exact: true }).click();
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
  let release!: () => void;
  const responseGate = new Promise<void>((resolve) => { release = resolve; });
  let saved!: () => void;
  const savedGate = new Promise<void>((resolve) => { saved = resolve; });
  let receipt!: { id: string };
  // Only response transport is delayed; the real handler writes the real task message/inbox.
  await page.route("**/api/l2/message", async (route) => {
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
  await savedGate;
  try {
    await expect(field).toHaveValue("");
    await walk.state("01-message-stored-response-pending", { visible: [bubble, pending], hidden: [alert, retry] });
    expect((await task()).messages.filter((row: { id: string }) => row.id === receipt.id)).toHaveLength(1);
    await field.fill("A different instruction I have not sent.");
    offline = true;
  } finally { release(); }
  await refreshFailed;
  await walk.state("02-accepted-refresh-failed-next-draft-retained", { visible: [bubble, field], hidden: [pending, alert, retry] });
  await expect(field).toHaveValue("A different instruction I have not sent.");
  const workers = await (await request.get("/fixture/workers")).json();
  expect(workers.pending[slug].filter((row: { id: string }) => row.id === receipt.id)).toHaveLength(1);
  expect(workers.calls).toHaveLength(0);
  offline = false;
  await page.reload();
  await walk.state("03-reconnected-message-still-sent-once", { visible: [bubble, field], hidden: [pending, alert, retry] });
  await expect(bubble).toHaveCount(1);
  await expect(field).toHaveValue("");
  expect((await task()).messages.filter((row: { id: string }) => row.id === receipt.id)).toHaveLength(1);
});

test("quick acceptance persists the recommendation, resumes the same L2 and clears Needs you", async ({ page, request }, info) => {
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
  await expect.poll(async () => (await task()).state).toBe("running");
  const resumed = await task();
  expect(resumed.state).toBe("running");
  const resolved = resumed.questions.find((row: { id: string; revision: number }) => row.id === question.id && row.revision === question.revision);
  expect(resolved.resolution).toMatchObject({ disposition: "answered", text: "Keep the bounded scope." });
  expect(resumed.session_id).toBe(`fixture-${slug}`);
  expect(resumed.messages.filter((row: { id: string }) => row.id === resolved.resolution.message_id)).toHaveLength(1);
  const workers = await (await request.get("/fixture/workers")).json();
  expect(workers.calls).toHaveLength(1);
  expect(workers.calls[0].prompt).toContain("Use this approach and continue: Keep the bounded scope.");
  await page.reload();
  await walk.state("03-decision-durable", { visible: [inline.getByText("Decision recorded", { exact: true })], hidden: [choice] });
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
  if (info.project.name === "phone") await page.getByRole("button", { name: "Task details", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page.getByRole("group", { name: "Stop this task?", exact: true }).getByRole("button", { name: "Stop", exact: true }).click();
  await expect.poll(async () => (await task()).daemon_request?.status).toBe("done");
  expect((await task()).state).toBe("blocked");
  expect((await task()).question).toBeNull();
  await page.reload();
  if (info.project.name === "phone") await page.getByRole("button", { name: "Task details", exact: true }).click();
  const resume = page.getByRole("button", { name: "Resume", exact: true });
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  await walk.state("01-stopped-with-operational-resume", {
    visible: [resume, page.getByRole("textbox", { name: "Message the L2", exact: true })],
    hidden: [conversation.locator("[data-question-id]"), page.getByRole("button", { name: "Stop", exact: true })],
  });
  const stateLine = page.locator(".task-state-line");
  const stateLineHeight = info.project.name === "phone" ? (await stateLine.boundingBox())!.height : 0;
  await page.route("**/api/task/action", (route) => route.fulfill({ status: 503, json: { error: "The resume request could not be saved." } }), { times: 1 });
  await resume.click();
  const error = page.getByRole("alert").filter({ hasText: "Could not resume. Try again." });
  await walk.state("02-resume-request-failed", { visible: [error, resume], hidden: [page.getByRole("button", { name: "Resuming…", exact: true })] });
  if (info.project.name === "phone") {
    await expect.poll(async () => (await stateLine.boundingBox())!.height, {
      message: "The resume error must not squeeze the readable task status into a vertical stack",
    }).toBeLessThanOrEqual(stateLineHeight + 1);
  }
  expect((await task()).state).toBe("blocked");
  expect((await workers()).calls).toHaveLength(0);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/task/action", async (route) => { await gate; await route.continue(); }, { times: 1 });
  try {
    await resume.click();
    const pending = page.getByRole("button", { name: "Resuming…", exact: true });
    await expect(pending).toBeDisabled();
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
  if (info.project.name === "phone") await page.getByRole("button", { name: "Task details", exact: true }).click();
  await walk.state("04-saved-session-resumed", {
    visible: [page.getByRole("button", { name: "Stop", exact: true }), page.getByRole("textbox", { name: "Message the L2", exact: true })],
    hidden: [resume, conversation.locator("[data-question-id]"), conversation.getByText("Decision recorded", { exact: true })],
  });
});
