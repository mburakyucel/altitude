import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureHost } from "./hostVoice";
import { walkthrough } from "./walkthrough";

test.use({ scenario: "tasks" });

test("reported open PR accepts follow-ups in the same held owner conversation", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = "prepare-index-migration";
  const before = await (await request.post("/fixture/reported", { data: {} })).json();
  const task = async () => (await (await request.get(`/api/task/atlas/${slug}`)).json());
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const send = conversation.getByRole("button", { name: "Send", exact: true });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  const mergeHeld = info.project.name === "phone" ? page.getByRole("status").filter({ hasText: "Merge held" }) : page.getByText("Merge held", { exact: true }).first();
  await walk.state("01-reported-empty-composer-merge-held", { visible: [field, mergeHeld], hidden: [] });
  await expect(send).toBeDisabled();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/l2/message", async (route) => {
    await gate;
    await route.continue();
  }, { times: 1 });
  await field.fill("Resolve the conflicts and retain the review hold.");
  await send.click();
  await walk.state("02-message-awaiting-receipt", { visible: [conversation.locator(".msg-row[data-pending]")], hidden: [] });
  release();
  await expect.poll(async () => (await task()).state).toBe("running");
  await expect(field).toHaveValue("");
  const resumed = await task();
  for (const key of ["attempt", "session_id", "worktree", "branch", "l2_engine", "prs", "paths", "hold_merge"]) expect(resumed[key]).toEqual(before[key]);
  expect(resumed.verified).toBeFalsy();
  expect(resumed.events.filter((row: { kind: string }) => row.kind === "report-superseded")).toHaveLength(1);
  const running = info.project.name === "phone" ? page.getByRole("status").filter({ hasText: "L2 replying to you" }) : page.getByText("L2 replying to you", { exact: true }).first();
  await expect(running).toBeVisible({ timeout: 25_000 });
  await walk.state("03-resumed-receipt-with-hold", { visible: [field, running, mergeHeld, conversation.getByText("Resolve the conflicts and retain the review hold.", { exact: true })], hidden: [conversation.locator(".msg-row[data-pending]")] });
  await field.fill("Keep the phone reading-position diagnosis in scope.");
  const secondReceipt = page.waitForResponse((response) => response.url().endsWith("/api/l2/message"));
  await send.click();
  expect((await secondReceipt).ok()).toBe(true);
  await expect(conversation.locator(".msg-row[data-pending]")).toBeHidden();
  await expect(field).toHaveValue("");
  const workers = await (await request.get("/fixture/workers")).json();
  expect(workers.calls).toHaveLength(1);
  expect(workers.calls[0].prompt).toContain("Resolve the conflicts and retain the review hold.");
  expect(workers.pending[slug].map((row: { text: string }) => row.text)).toEqual(["Keep the phone reading-position diagnosis in scope."]);
  await page.reload();
  await walk.state("04-reload-retains-both-follow-ups", { visible: [field, conversation.getByText("Keep the phone reading-position diagnosis in scope.", { exact: true })], hidden: [] });
  expect((await task()).messages.filter((row: { role: string }) => row.role === "burak")).toHaveLength(2);
});

test("reported continuation refuses a PR that closed before send and keeps the words on the message", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await request.post("/fixture/reported", { data: { pr_state: "MERGED" } });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  await walk.open("/projects/atlas/tasks/prepare-index-migration");
  await field.fill("Resolve the conflicts.");
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  const message = conversation.locator(".msg-row[data-mine]").filter({ hasText: "Resolve the conflicts." });
  await walk.state("01-closed-pr-refused-on-message", { visible: [field, message.getByRole("alert").filter({ hasText: /^Not sent: / }), message.getByRole("button", { name: "Edit", exact: true })], hidden: [] });
  await expect(field).toHaveValue("");
  const task = await (await request.get("/api/task/atlas/prepare-index-migration")).json();
  expect(task.state).toBe("reported");
  expect(task.messages.some((row: { text: string }) => row.text === "Resolve the conflicts.")).toBe(false);
});

test("lost reported-message receipt settles one saved message from the record, through reload, without replay", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await request.post("/fixture/reported", { data: {} });
  let submissions = 0;
  await page.route("**/api/l2/message", async (route) => {
    submissions++;
    expect((await route.fetch()).ok()).toBe(true);
    await route.abort("connectionfailed");
  });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const doubt = [conversation.getByText("Unconfirmed", { exact: true }), conversation.getByRole("alert"), conversation.getByRole("button", { name: "Retry", exact: true })];
  const text = "Retain this follow-up even if its receipt is lost.";
  await walk.open("/projects/atlas/tasks/prepare-index-migration");
  await field.fill(text);
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  const bubble = conversation.locator(".bubble").filter({ hasText: text });
  // The read that lists the send id settles the lost receipt: one stored message, never offered for resending.
  await walk.state("01-lost-receipt-settled-from-record", { visible: [field, bubble], hidden: doubt });
  await expect(field).toHaveValue("");
  await page.reload();
  await walk.state("02-reload-retains-saved-row-without-resend", { visible: [field, bubble], hidden: doubt });
  await expect(field).toHaveValue("");
  await expect(bubble).toHaveCount(1);
  const task = await (await request.get("/api/task/atlas/prepare-index-migration")).json();
  expect(task.messages.filter((row: { text: string }) => row.text === text)).toHaveLength(1);
  expect(task.hold_merge).toBe("Operator review required");
  expect(submissions).toBe(1);
});

test("reported composer retains voice listening, cancellation and denied states", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await request.post("/fixture/reported", { data: {} });
  // Host voice hears Chromium's fake microphone through the fixture host.
  await fixtureHost(page);
  await walk.open("/projects/atlas/tasks/prepare-index-migration");
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const mic = page.getByRole("button", { name: "Start voice input", exact: true });
  const cancel = page.getByRole("button", { name: "Cancel voice input", exact: true });
  await field.fill("Keep the typed draft.");
  await mic.click();
  await walk.state("01-reported-listening", { visible: [cancel, page.getByLabel("Recording time", { exact: true })], hidden: [mic] });
  await cancel.click();
  await walk.state("02-cancel-restores-typed-draft", { visible: [field, mic], hidden: [cancel] });
  await expect(field).toHaveValue("Keep the typed draft.");
  await page.evaluate(() => {
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
      configurable: true, value: async () => { throw new DOMException("Denied", "NotAllowedError"); },
    });
  });
  await mic.click();
  await walk.state("03-microphone-denied-typing-available", { visible: [field, page.getByText("Microphone blocked in the browser. Typing works.", { exact: true })], hidden: [cancel] });
  await expect(field).toHaveValue("Keep the typed draft.");
});
