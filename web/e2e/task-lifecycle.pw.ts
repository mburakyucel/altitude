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
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page.getByRole("group", { name: "Stop this task?", exact: true }).getByRole("button", { name: "Stop", exact: true }).click();
  await expect.poll(async () => (await task()).daemon_request?.status).toBe("done");
  expect((await task()).state).toBe("blocked");
  expect((await workers()).workers[resumed.agent_id].state).toBe("stopped");
  await page.reload();
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

test("an operator decision persists its option and note, resumes the same L2 and clears Needs you", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "choose-validation-scope";
  const task = async () => (await (await request.get(`/api/task/atlas/${slug}`)).json());
  await walk.open("/");
  const card = page.getByRole("article", { name: "Choose validation scope", exact: true });
  await walk.state("01-needs-you", { visible: [card], hidden: [] });
  await card.getByRole("link", { name: "More context", exact: true }).click();
  const options = page.locator(".decision-options-page");
  const choice = options.getByRole("button", { name: "Keep the bounded scope", exact: true });
  await walk.state("02-recorded-question", { visible: [choice, page.getByRole("heading", { name: "Where this came from", exact: true })], hidden: [] });
  await options.getByPlaceholder("Add a note for the L2 (optional)").fill("Validate the core flow with fixtures.");
  await choice.click();
  await expect.poll(async () => (await task()).daemon_request?.status).toBe("done");
  const resumed = await task();
  expect(resumed.state).toBe("running");
  expect(resumed.decision).toMatchObject({ key: "A", option: "Keep the bounded scope", note: "Validate the core flow with fixtures." });
  expect(resumed.session_id).toBe(`fixture-${slug}`);
  expect(resumed.messages.some((row: { text: string }) => row.text.includes("Decision: A (Keep the bounded scope). Validate the core flow with fixtures."))).toBe(true);
  const workers = await (await request.get("/fixture/workers")).json();
  expect(workers.calls).toHaveLength(1);
  expect(workers.calls[0].prompt).toContain("Validate the core flow with fixtures.");
  await page.reload();
  await walk.state("03-decision-durable", { visible: [page.getByRole("status").filter({ hasText: /Decided .*Keep the bounded scope/ })], hidden: [choice] });
  await walk.open("/");
  await walk.state("04-needs-you-cleared", { visible: [page.getByRole("heading", { name: "Needs you", exact: true })], hidden: [card] });
  const overview = await (await request.get("/api/overview")).json();
  expect(overview.queue.some((row: { slug: string }) => row.slug === slug)).toBe(false);
});
