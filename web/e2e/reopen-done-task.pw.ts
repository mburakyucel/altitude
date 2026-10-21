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
  const reopens = page.getByPlaceholder("Message the L2 to reopen the task", { exact: true });
  const done = conversation.getByText("Done · Fictional completed delivery.", { exact: true });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await walk.state("01-done-composer-reopens", { visible: [field, reopens], hidden: [done, page.getByRole("button", { name: /^Task model/ })] });
  await expect(send).toBeDisabled();

  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/l2/message", async (route) => {
    await gate;
    await route.continue();
  }, { times: 1 });
  await field.fill("Which parts of the contract did the notes leave out?");
  await send.click();
  await walk.state("02-follow-up-awaiting-receipt", { visible: [conversation.locator(".msg-row[data-pending]")], hidden: [] });
  release();
  await expect.poll(async () => (await task()).state).toBe("running");
  await expect(field).toHaveValue("");
  const reopened = await task();
  for (const key of ["attempt", "session_id", "worktree", "branch", "l2_engine", "prs"]) expect(reopened[key]).toEqual(before[key]);
  const { calls } = await (await request.get("/fixture/workers")).json();
  expect(calls.at(-1).prompt).toContain("The operator's message reopens this task");
  expect(calls.at(-1).prompt).toContain("Which parts of the contract did the notes leave out?");
  const show = conversation.getByRole("button", { name: "Show", exact: true });
  await walk.state("03-reopened-earlier-completion-and-follow-up", {
    visible: [done, show, field, conversation.getByText("Which parts of the contract did the notes leave out?", { exact: true })],
    hidden: [reopens, conversation.locator(".msg-row[data-pending]")],
  });

  await walk.state("04-earlier-digest-shown", {
    action: () => show.click(),
    visible: [conversation.getByRole("button", { name: "Hide", exact: true }), conversation.locator(".coordination-body").getByText("Fictional completed delivery.", { exact: true })],
    hidden: [show],
  });
  await page.reload();
  await walk.state("05-reload-keeps-history", { visible: [done, field], hidden: [reopens] });
});

test("a rejected task stays read-only", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/projects/atlas/tasks/retire-unused-prototype");
  await walk.state("01-rejected-read-only", {
    visible: [page.getByText("No messages on this task.", { exact: false }).or(page.getByRole("region", { name: "Task conversation", exact: true }))],
    hidden: [page.getByRole("textbox", { name: "Message the L2", exact: true })],
  });
});
