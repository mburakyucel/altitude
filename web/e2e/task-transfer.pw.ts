import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";
import { fixtureHost } from "./hostVoice";

test.use({ serviceScript: "task-transfer-service.py" });

test("fixture control only addresses its own created tasks", async ({ request }) => {
  for (const slug of ["../outside", "unknown-task"]) {
    const response = await request.post("/fixture/transfer", { data: { slug, ready: true } });
    expect(response.status()).toBe(400);
    expect(await response.json()).toEqual({ error: "Unknown fixture task" });
  }
});

const path = (slug: string) => `/api/task/atlas/${slug}`;
const card = (page: Page) => page.getByRole("region", { name: "Task conversation", exact: true })
  .getByRole("region", { name: "Engine transfer", exact: true });
const draft = (page: Page) => page.getByRole("textbox", { name: "Message the L2", exact: true });
async function start(page: Page) {
  await page.getByRole("button", { name: /Task details$/ }).click();
  await page.getByRole("dialog", { name: "Task details", exact: true }).getByRole("button", { name: "Switch engine…", exact: true }).click();
}

test("patient transfer cancels without losing the draft, then readiness starts a fresh owner", async ({ page, request }, info) => {
  const slug = "transfer-running-work";
  const read = async () => (await (await request.get(path(slug))).json());
  const before = await read();
  const target = before.transfer.targets.find((item: { available: boolean }) => item.available);
  const walk = walkthrough(page, info);
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await draft(page).fill("Keep this unsent instruction.");
  await draft(page).evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(5, 9));
  await start(page);
  const confirm = page.getByRole("dialog", { name: `Switch to ${target.label}?`, exact: true });
  await walk.state("01-confirm-context-and-cost", { visible: [confirm, confirm.getByText("Some session context may not carry over. The new engine uses extra tokens to get up to speed.")], hidden: [card(page)] });
  await page.keyboard.press("Escape");
  await expect(confirm).toBeHidden();
  expect((await read()).daemon_request).toBeFalsy();
  await expect(draft(page)).toHaveValue("Keep this unsent instruction.");
  await start(page);
  await confirm.getByRole("button", { name: "Prepare switch", exact: true }).click();
  await walk.state("02-preparing-card-stop-and-draft", { visible: [card(page), card(page).getByRole("button", { name: "Switch now", exact: true }), card(page).getByRole("button", { name: "Cancel switch", exact: true }), page.getByRole("button", { name: "Stop", exact: true }).first()], hidden: [confirm] });
  expect((await read()).attempt).toBe(before.attempt);
  await card(page).getByRole("button", { name: "Cancel switch", exact: true }).click();
  await expect.poll(async () => (await read()).transfer.request.status).toBe("cancelled");
  await walk.state("03-cancelled-restores-owner", { visible: [draft(page)], hidden: [card(page).getByRole("button", { name: "Switch now", exact: true })] });
  await expect(draft(page)).toHaveValue("Keep this unsent instruction.");
  await start(page);
  await confirm.getByRole("button", { name: "Prepare switch", exact: true }).click();
  await expect(card(page)).toBeVisible();
  const response = await request.post("/fixture/transfer", { data: { slug, ready: true, launch: true } });
  expect(response.ok()).toBe(true);
  await expect.poll(async () => (await read()).attempt).toBe(before.attempt + 1);
  await expect(card(page).getByRole("button", { name: "Cancel switch", exact: true })).toBeHidden();
  const after = await read();
  expect(after.l2_engine).toBe(target.engine);
  for (const key of ["worktree", "branch", "hold_merge", "paths", "prs"]) expect(after[key]).toEqual(before[key]);
  expect(after.session_id).not.toBe(before.session_id);
  await walk.state("04-new-owner-draft-and-hold-retained", { visible: [draft(page), page.locator('[data-tone="held"]').filter({ hasText: "Merge held" }).first()], hidden: [confirm] });
  await expect(draft(page)).toHaveValue("Keep this unsent instruction.");
});

test("explicit switch now replaces a running owner and preserves the merge hold", async ({ page, request }, info) => {
  const slug = "transfer-running-work";
  const read = async () => (await (await request.get(path(slug))).json());
  const before = await read();
  const walk = walkthrough(page, info);
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await start(page);
  await page.getByRole("button", { name: "Prepare switch", exact: true }).click();
  await card(page).getByRole("button", { name: "Switch now", exact: true }).click();
  const confirm = page.getByRole("dialog", { name: "Switch without a fresh note?", exact: true });
  await walk.state("01-switch-now-confirmation", { visible: [confirm, confirm.getByRole("button", { name: "Keep waiting", exact: true })], hidden: [] });
  await confirm.getByRole("button", { name: "Keep waiting", exact: true }).click();
  await card(page).getByRole("button", { name: "Switch now", exact: true }).click();
  await confirm.getByRole("button", { name: "Switch now", exact: true }).click();
  await expect.poll(async () => (await read()).state).toBe("queued");
  await walk.state("02-queued-has-no-conflicting-controls", { visible: [page.locator('[data-tone="held"]').filter({ hasText: "Merge held" }).first()], hidden: [confirm, card(page).getByRole("button", { name: "Cancel switch", exact: true })] });
  expect((await request.post("/fixture/transfer", { data: { slug, launch: true } })).ok()).toBe(true);
  const after = await read();
  expect(after.attempt).toBe(before.attempt + 1);
  expect(after.questions).toEqual(before.questions.map((question: Record<string, unknown>) => ({ ...question, state: after.state })));
  expect(after.hold_merge).toBe(before.hold_merge);
  await walk.state("03-new-attempt-hold-kept", { visible: [page.locator('[data-tone="held"]').filter({ hasText: "Merge held" }).first()], hidden: [confirm] });
});

test("a blocked question owner switches from saved context without answering the question", async ({ page, request }, info) => {
  const slug = "transfer-question-work";
  const read = async () => (await (await request.get(path(slug))).json());
  const before = await read();
  expect(before.state).toBe("blocked");
  expect(before.questions.some((question: { status: string }) => question.status === "open")).toBe(true);
  expect(before.transfer.preparation).toBe(false);
  const target = before.transfer.targets.find((item: { available: boolean }) => item.available);
  const walk = walkthrough(page, info);
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await start(page);
  const confirm = page.getByRole("dialog", { name: `Switch to ${target.label}?`, exact: true });
  await walk.state("01-blocked-question-saved-context", { visible: [confirm, confirm.getByText(`${target.label} continues from the saved task record.`, { exact: true })], hidden: [confirm.getByRole("button", { name: "Prepare switch", exact: true })] });
  await confirm.getByRole("button", { name: `Switch to ${target.label}`, exact: true }).click();
  await expect.poll(async () => (await read()).state).toBe("queued");
  expect((await request.post("/fixture/transfer", { data: { slug, launch: true } })).ok()).toBe(true);
  const after = await read();
  expect(after.attempt).toBe(before.attempt + 1);
  expect(after.questions).toEqual(before.questions.map((question: Record<string, unknown>) => ({ ...question, state: after.state })));
  expect(after.hold_merge).toBe(before.hold_merge);
  await walk.state("02-new-owner-question-stays-open", { visible: [page.getByText("Keep the bounded validation scope?", { exact: true }).first(), page.locator('[data-tone="held"]').filter({ hasText: "Merge held" }).first()], hidden: [confirm, page.getByRole("button", { name: "Resume", exact: true })] });
});

test("quota stop offers saved-context confirmation; pinned work explains unavailability", async ({ page, request }, info) => {
  const slug = "transfer-quota-work";
  const row = await (await request.get(path(slug))).json();
  const target = row.transfer.targets.find((item: { available: boolean }) => item.available);
  const walk = walkthrough(page, info);
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await walk.state("01-quota-offers-switch", { visible: [page.getByRole("button", { name: `Switch to ${target.label}`, exact: true })], hidden: [] });
  expect(row.transfer.resume_hold).toBeTruthy();
  if (info.project.name === "desktop") await expect(page.getByRole("button", { name: "Resume", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: `Switch to ${target.label}`, exact: true }).click();
  const confirm = page.getByRole("dialog", { name: `Switch to ${target.label}?`, exact: true });
  await walk.state("02-quota-saved-context", { visible: [confirm, confirm.getByText(`${target.label} continues from the saved task record.`, { exact: true })], hidden: [confirm.getByRole("button", { name: "Prepare switch", exact: true })] });
  await confirm.getByRole("button", { name: `Switch to ${target.label}`, exact: true }).click();
  await expect.poll(async () => (await (await request.get(path(slug))).json()).state).toBe("queued");
  await walk.open("/projects/atlas/tasks/transfer-pinned-work");
  await page.getByRole("button", { name: /Task details$/ }).click();
  const details = page.getByRole("dialog", { name: "Task details", exact: true });
  const entry = details.getByRole("button", { name: "Switch engine…", exact: true });
  await expect(entry).toHaveAttribute("aria-disabled", "true");
  await entry.click({ force: true });
  await walk.state("03-pin-explains-unavailable", { visible: [details, details.locator(".transfer-entry [role=status]")], hidden: [confirm] });
});

test("lost switch response remains uncertain with Stop available and reconciles without a duplicate", async ({ page, request }, info) => {
  const slug = "transfer-running-work";
  const walk = walkthrough(page, info);
  let posts = 0;
  await page.route("**/api/task/action", async (route) => {
    if (route.request().postDataJSON().action !== "handoff") return route.continue();
    posts++;
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    await route.abort("connectionfailed");
  });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await draft(page).fill("Retain this draft across the lost receipt.");
  await start(page);
  await page.getByRole("button", { name: "Prepare switch", exact: true }).click();
  await walk.state("01-unconfirmed-stop-retained", { visible: [card(page).getByRole("button", { name: "Check status", exact: true }), page.getByRole("button", { name: "Stop", exact: true }).first()], hidden: [page.getByRole("button", { name: "Prepare switch", exact: true })] });
  await card(page).getByRole("button", { name: "Check status", exact: true }).focus();
  await page.keyboard.press("Escape");
  expect((await (await request.get(path(slug))).json()).daemon_request.operation).toBe("handoff");
  await card(page).getByRole("button", { name: "Check status", exact: true }).click();
  await walk.state("02-reconnected-to-accepted-request", { visible: [card(page).getByRole("button", { name: "Cancel switch", exact: true })], hidden: [card(page).getByRole("button", { name: "Check status", exact: true })] });
  expect(posts).toBe(1);
  await expect(draft(page)).toHaveValue("Retain this draft across the lost receipt.");
  await page.reload();
  await walk.state("03-reload-restores-durable-controls", { visible: [card(page).getByRole("button", { name: "Switch now", exact: true })], hidden: [] });
});

test("new permission denial removes transfer actions", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  await page.route("**/api/task/action", route => route.request().postDataJSON().action === "handoff"
    ? route.fulfill({ status: 403, json: { error: "Permission denied" } }) : route.continue());
  await walk.open("/projects/atlas/tasks/transfer-running-work");
  await start(page);
  await page.getByRole("button", { name: "Prepare switch", exact: true }).click();
  await walk.state("01-permission-denied", { visible: [page.getByText("You do not have permission to switch this task.", { exact: true })], hidden: [page.getByRole("button", { name: "Prepare switch", exact: true }), card(page).getByRole("button", { name: "Switch now", exact: true })] });
});

test("@chromium listening survives confirmation and an accepted transfer without sending the draft", async ({ page, request }, info) => {
  const slug = "transfer-running-work";
  const host = await fixtureHost(page);
  host.live = "preserve the recorded instruction";
  const walk = walkthrough(page, info);
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const stopVoice = conversation.getByRole("button", { name: "Stop voice input", exact: true });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await draft(page).fill("Unsent text.");
  await conversation.getByRole("button", { name: "Start voice input", exact: true }).click();
  await expect(draft(page)).toHaveValue("Unsent text. preserve the recorded instruction");
  await start(page);
  await walk.state("01-listening-during-confirmation", { visible: [page.getByRole("button", { name: "Prepare switch", exact: true })], hidden: [] });
  await page.keyboard.press("Escape");
  await expect(stopVoice).toBeVisible();
  expect(host.finals).toBe(0);
  await start(page);
  await page.getByRole("button", { name: "Prepare switch", exact: true }).click();
  await walk.state("02-listening-while-preparing", { visible: [stopVoice, card(page)], hidden: [] });
  expect((await request.post("/fixture/transfer", { data: { slug, ready: true } })).ok()).toBe(true);
  await expect.poll(async () => (await (await request.get(path(slug))).json()).state).toBe("queued");
  await walk.state("03-listening-retained-while-queued", { visible: [stopVoice, draft(page)], hidden: [] });
  expect(host.finals).toBe(0);
  expect((await request.post("/fixture/transfer", { data: { slug, launch: true } })).ok()).toBe(true);
  await expect(card(page).getByRole("button", { name: "Cancel switch", exact: true })).toBeHidden();
  await walk.state("04-listening-retained-on-new-attempt", { visible: [stopVoice, draft(page)], hidden: [] });
  await expect(draft(page)).toHaveValue("Unsent text. preserve the recorded instruction");
  expect(host.opened).toBe(1);
  expect(host.finals).toBe(0);
  const task = await (await request.get(path(slug))).json();
  expect(task.messages.some((row: { text: string }) => row.text.includes("Unsent text."))).toBe(false);
  // Escape on the unobstructed recording cancels voice before task Stop.
  await draft(page).focus();
  await page.keyboard.press("Escape");
  await expect(stopVoice).toBeHidden();
  expect((await (await request.get(path(slug))).json()).state).toBe("running");
});

test("@desktop-only compact transfer header remains readable at 1024 pixels", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "Desktop density state");
  await page.setViewportSize({ width: 1024, height: 768 });
  const walk = walkthrough(page, info);
  await walk.open("/projects/atlas/tasks/transfer-running-work");
  await start(page);
  await page.getByRole("button", { name: "Prepare switch", exact: true }).click();
  await walk.state("01-1024-preparing-with-stop", { visible: [card(page), page.getByRole("button", { name: "Stop", exact: true }).first(), page.locator('[data-tone="held"]').filter({ hasText: "Merge held" }).first()], hidden: [] });
  expect(await page.locator(".task-header .task-chips").evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});

test("Stop supersedes preparation and uncertain termination never launches a replacement", async ({ page, request }, info) => {
  const slug = "transfer-running-work";
  const read = async () => (await (await request.get(path(slug))).json());
  const before = await read();
  const walk = walkthrough(page, info);
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await start(page);
  await page.getByRole("button", { name: "Prepare switch", exact: true }).click();
  await expect(card(page)).toBeVisible();
  expect((await request.post("/fixture/transfer", { data: { slug, unknown_termination: true, stop_failure: true } })).ok()).toBe(true);
  await page.getByRole("button", { name: "Stop", exact: true }).first().click();
  await expect.poll(async () => (await read()).steering.state).toBe("stop_unconfirmed");
  await walk.state("01-stop-unconfirmed", { visible: [page.getByText(/Stop unconfirmed/).first(), page.getByRole("button", { name: "Check status", exact: true }).first()], hidden: [card(page).getByRole("button", { name: "Cancel switch", exact: true })] });
  expect((await read()).attempt).toBe(before.attempt);
  expect((await read()).next_engine).toBeFalsy();
  expect((await request.post("/fixture/transfer", { data: { slug, unknown_termination: false } })).ok()).toBe(true);
  // Polling can confirm the stop before a manual recheck; observe the restored action without clicking it.
  await walk.state("02-stop-confirmed-continue-restored", { visible: [page.getByRole("button", { name: "Continue", exact: true }).first()], hidden: [page.getByText(/Stop unconfirmed/).first(), card(page).getByRole("button", { name: "Switch now", exact: true })] });
});

test("switch-now waits for confirmed old-worker termination", async ({ page, request }, info) => {
  const slug = "transfer-running-work";
  const read = async () => (await (await request.get(path(slug))).json());
  const before = await read();
  const walk = walkthrough(page, info);
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await start(page);
  await page.getByRole("button", { name: "Prepare switch", exact: true }).click();
  expect((await request.post("/fixture/transfer", { data: { slug, unknown_termination: true } })).ok()).toBe(true);
  await card(page).getByRole("button", { name: "Switch now", exact: true }).click();
  await page.getByRole("dialog", { name: "Switch without a fresh note?", exact: true }).getByRole("button", { name: "Switch now", exact: true }).click();
  await expect.poll(async () => (await read()).steering.state).toBe("stop_unconfirmed");
  await walk.state("01-transfer-stop-unconfirmed", { visible: [page.getByText(/Stop unconfirmed/).first(), page.getByRole("button", { name: "Check status", exact: true }).first()], hidden: [card(page).getByRole("button", { name: "Switch now", exact: true }), card(page).getByRole("button", { name: "Cancel switch", exact: true })] });
  const after = await read();
  expect(after.attempt).toBe(before.attempt);
  expect(after.next_engine).toBeFalsy();
  expect(after.transfer.request.status).toBe("refused");
});

for (const outcome of ["question", "report", "unavailable"] as const) test(`preparation yields safely to ${outcome}`, async ({ page, request }, info) => {
  const slug = "transfer-running-work";
  const read = async () => (await (await request.get(path(slug))).json());
  const before = await read();
  const walk = walkthrough(page, info);
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await draft(page).fill("Retain the next instruction.");
  await start(page);
  await page.getByRole("button", { name: "Prepare switch", exact: true }).click();
  await expect(card(page).getByRole("button", { name: "Cancel switch", exact: true })).toBeVisible();
  expect((await request.post("/fixture/transfer", { data: { slug, outcome } })).ok()).toBe(true);
  await expect.poll(async () => (await read()).transfer.request.status).toBe(outcome === "question" ? "cancelled" : "refused");
  await walk.state(`01-${outcome}-owns-the-task`, { visible: [draft(page), conversation.getByText(/Switch cancelled|did not finish|Report finished|Switch failed/).last()], hidden: [card(page).getByRole("button", { name: "Cancel switch", exact: true }), card(page).getByRole("button", { name: "Switch now", exact: true })] });
  const after = await read();
  expect(after.attempt).toBe(before.attempt);
  expect(after.next_engine).toBeFalsy();
  expect(after.hold_merge).toBe(before.hold_merge);
  await expect(draft(page)).toHaveValue("Retain the next instruction.");
  if (outcome === "question") {
    await expect(conversation.getByText("Which validation scope should the new engine preserve?", { exact: true }).first()).toBeVisible();
    expect(after.questions.some((question: { status: string }) => question.status === "open")).toBe(true);
    await expect(page.getByRole("button", { name: "Resume", exact: true })).toBeHidden();
  }
  if (outcome === "report") expect(after.transfer.request.note).toContain("report");
});
