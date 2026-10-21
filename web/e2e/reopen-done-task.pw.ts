import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ scenario: "tasks" });

test("the operator's message reopens a done task, which keeps its earlier completion in the conversation", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "document-search-contract";
  const task = async () => (await (await request.get(`/api/task/atlas/${slug}`)).json());
  const before = await task();
  expect(before.state).toBe("done");
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const send = conversation.getByRole("button", { name: "Send", exact: true });
  const reopens = page.getByText("Sending reopens the task with your message.", { exact: true });
  const done = conversation.getByText("Done · Fictional completed delivery.", { exact: true });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await walk.state("01-done-composer-reopens", { visible: [field, reopens], hidden: [done, page.getByRole("button", { name: /^Task model/ })] });
  await expect(send).toBeDisabled();

  const question = "Which parts of the contract did the notes leave out?";
  const row = conversation.locator(".msg-row").filter({ hasText: question });
  const retry = row.getByRole("button", { name: "Retry", exact: true });
  await page.route("**/api/l2/message", (route) => route.fulfill({ status: 409, json: { error: "Controlled refusal" } }), { times: 1 });
  await field.fill(question);
  await walk.state("02-refused-stays-done", {
    action: () => send.click(),
    visible: [row.getByRole("alert").filter({ hasText: "Not sent: Controlled refusal." }), retry, reopens],
    hidden: [done],
  });
  expect((await task()).state).toBe("done");

  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/l2/message", async (route) => {
    await gate;
    await route.continue();
  }, { times: 1 });
  await walk.state("03-follow-up-awaiting-receipt", { action: () => retry.click(), visible: [conversation.locator(".msg-row[data-pending]")], hidden: [retry] });
  release();
  await expect.poll(async () => (await task()).state).toBe("running");
  await expect(field).toHaveValue("");
  const reopened = await task();
  for (const key of ["attempt", "session_id", "worktree", "branch", "l2_engine", "prs"]) expect(reopened[key]).toEqual(before[key]);
  const { calls } = await (await request.get("/fixture/workers")).json();
  expect(calls.at(-1).prompt).toContain("The operator's message reopens this task");
  expect(calls.at(-1).prompt).toContain(question);
  const show = conversation.getByRole("button", { name: "Show", exact: true });
  await walk.state("04-reopened-earlier-completion-and-follow-up", {
    visible: [done, show, field, conversation.getByText(question, { exact: true })],
    hidden: [reopens, conversation.locator(".msg-row[data-pending]")],
  });

  const hide = conversation.getByRole("button", { name: "Hide", exact: true });
  const digest = conversation.locator(".coordination-body").getByText("Fictional completed delivery.", { exact: true });
  await walk.state("05-earlier-digest-shown", { action: () => show.click(), visible: [hide, digest], hidden: [show] });
  await walk.state("06-earlier-digest-hidden", { action: () => hide.click(), visible: [show], hidden: [hide, digest] });
  await page.reload();
  await walk.state("07-reload-keeps-history", { visible: [done, show, field, conversation.getByText(question, { exact: true })], hidden: [reopens] });
});

test("a rejected task stays read-only", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/projects/atlas/tasks/retire-unused-prototype");
  await walk.state("01-rejected-read-only", {
    visible: [page.getByText("No messages on this task.", { exact: false }).or(page.getByRole("region", { name: "Task conversation", exact: true }))],
    hidden: [page.getByRole("textbox", { name: "Message the L2", exact: true })],
  });
});
