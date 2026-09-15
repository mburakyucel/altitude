import { expect, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "question-feedback-service.py" });
const taskPath = "/projects/atlas/tasks/choose-rollout-settings";
const read = async (request: APIRequestContext) => (await request.get("/api/task/atlas/choose-rollout-settings")).json();
const remaining = async (request: APIRequestContext) => (await (await request.get("/api/overview")).json()).queue;

test("mixed custom, preset and plain answers remain conversational until the owner interprets them", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const initial = await read(request);
  const [retention, region, recipient] = initial.question_group.questions;
  await walk.open("/");
  const card = page.getByRole("article", { name: "Choose rollout settings", exact: true });
  const own = card.locator(`[data-question-id="${recipient.id}"]`);
  await walk.state("01-plain-question-and-preset-choices", {
    visible: [card.getByRole("button", { name: "7 days", exact: true }), own.getByRole("textbox")],
    hidden: [card.locator(`[data-question-id="${retention.id}"] textarea`), card.getByRole("button", { name: /microphone|voice/i })],
  });
  await card.getByRole("button", { name: "East", exact: true }).click();
  const retentionCard = card.locator(`[data-question-id="${retention.id}"]`);
  await retentionCard.getByRole("button", { name: "Other…", exact: true }).click();
  await expect(retentionCard.getByRole("textbox")).toBeFocused();
  await retentionCard.getByRole("textbox").fill("21 days");
  await walk.state("02-custom-and-preset-staged-together", {
    visible: [retentionCard.getByRole("textbox"), card.getByRole("button", { name: "Send 2 answers", exact: true })],
    hidden: [card.getByText("Sent to L2", { exact: true })],
  });
  if (info.project.name === "phone") {
    // Simulate the reduced visual viewport; this does not launch a native software keyboard.
    await page.setViewportSize({ width: 390, height: 480 });
    await expect(card.getByRole("button", { name: "Send 2 answers", exact: true })).toBeInViewport();
    await walk.state("02b-simulated-keyboard-keeps-send-reachable", {
      visible: [retentionCard.getByRole("textbox"), card.getByRole("button", { name: "Send 2 answers", exact: true })],
      hidden: [page.getByRole("navigation", { name: "Primary", exact: true })],
    });
    await page.setViewportSize({ width: 390, height: 844 });
  }
  await card.getByRole("button", { name: "Send 2 answers", exact: true }).click();
  await expect.poll(async () => (await remaining(request)).length).toBe(1);
  const submitted = await read(request);
  const sent = submitted.question_group.questions.slice(0, 2);
  expect(sent.map((q: any) => q.response.text)).toEqual(["21 days", "Use the east region."]);
  expect(sent.every((q: any) => q.status === "open" && q.resolution === null)).toBe(true);
  expect(sent[0].response.message_id).toBe(sent[1].response.message_id);
  expect(submitted.hold_merge).toBe(initial.hold_merge);
  await card.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const group = conversation.locator(`[data-group-id="${initial.question_group.id}"]`);
  await group.locator(`[data-question-id="${recipient.id}"]`).getByRole("textbox").scrollIntoViewIfNeeded();
  await expect(group.locator(`[data-question-id="${recipient.id}"]`).getByRole("textbox")).toBeInViewport();
  await walk.state("03-remaining-question-beneath-sent-receipts", {
    visible: [group.locator(`[data-question-id="${retention.id}"]`).getByText("Sent to L2", { exact: true }), group.locator(`[data-question-id="${recipient.id}"]`).getByRole("textbox")],
    hidden: [group.getByText("Decision recorded", { exact: true }), group.locator(`[data-question-id="${retention.id}"] textarea`)],
  });
  await group.locator(`[data-question-id="${recipient.id}"]`).getByRole("textbox").fill("Release team");
  await group.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect.poll(async () => (await remaining(request)).length).toBe(0);
  expect((await read(request)).question_group.questions.every((q: any) => q.resolution === null)).toBe(true);
  expect((await request.post("/fixture/checkpoint")).ok()).toBe(true);
  await page.reload();
  await expect(group.getByText("Decision recorded", { exact: true })).toHaveCount(3);
  await walk.state("04-owner-records-the-actual-answers", {
    visible: [group.locator(`[data-question-id="${retention.id}"]`).getByText("21 days", { exact: true }), page.getByRole("textbox", { name: "Message the L2", exact: true })],
    hidden: [group.getByRole("textbox"), group.getByRole("button", { name: /^Send/ })],
  });
  expect((await read(request)).hold_merge).toBe(initial.hold_merge);
});

test("a saved response clears a lost-reply error in the mounted conversation", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const initial = await read(request);
  const [retention, region] = initial.question_group.questions;
  await walk.open(taskPath);
  const group = page.locator(`[data-group-id="${initial.question_group.id}"]`);
  const card = group.locator(`[data-question-id="${retention.id}"]`);
  const independent = group.locator(`[data-question-id="${region.id}"]`);
  await independent.getByRole("button", { name: "Other…", exact: true }).click();
  await independent.getByRole("textbox").fill("   ");
  await card.getByRole("button", { name: "Other…", exact: true }).click();
  await card.getByRole("textbox").fill("21 days");
  // Hold the read at its initial state until the lost response has shown its recoverable error.
  await page.route("**/api/task/atlas/choose-rollout-settings", route => route.fulfill({ json: initial }));
  await page.route("**/api/decide", async route => {
    const saved = await route.fetch();
    expect(saved.ok()).toBe(true);
    await route.abort("connectionfailed");
  }, { times: 1 });
  await group.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect(group.getByRole("alert")).toContainText("Could not send answers");
  await expect(card.getByRole("textbox")).toHaveValue("21 days");
  await page.unroute("**/api/task/atlas/choose-rollout-settings");
  await expect(card.getByText("Sent to L2", { exact: true })).toBeVisible({ timeout: 25_000 });
  await expect(independent.getByRole("textbox")).toHaveValue("   ");
  await walk.state("saved-response-reconciles-lost-reply", {
    visible: [card.getByText("Sent to L2", { exact: true }), independent.getByRole("textbox")],
    hidden: [group.getByRole("alert"), group.getByRole("button", { name: "Retry", exact: true }), card.getByRole("textbox")],
  });
  const final = await read(request);
  expect(final.question_group.questions[0].resolution).toBeNull();
  expect(final.messages.filter((message: any) => message.id === final.question_group.questions[0].response.message_id)).toHaveLength(1);
  expect((await remaining(request)).length).toBe(2);
});

test("a question submitted through Other gets a conversational reply and a fresh question", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const initial = await read(request);
  const retention = initial.question_group.questions[0];
  await walk.open(taskPath);
  const group = page.locator(`[data-group-id="${initial.question_group.id}"]`);
  const card = group.locator(`[data-question-id="${retention.id}"]`);
  await card.getByRole("button", { name: "Other…", exact: true }).click();
  await card.getByRole("textbox").fill("Why seven or fourteen days?");
  await group.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect(card.getByText("Sent to L2", { exact: true })).toBeVisible();
  expect((await read(request)).question_group.questions[0].resolution).toBeNull();
  expect((await request.post("/fixture/checkpoint")).ok()).toBe(true);
  await page.reload();
  const current = (await read(request)).question_group.questions.find((q: any) => q.id === retention.id);
  expect(current.revision).toBe(retention.revision + 1);
  expect(current.response).toBeNull();
  await walk.state("followup-returns-a-fresh-prompt", {
    visible: [group.getByText("What retention period would you prefer?", { exact: true }), group.getByRole("button", { name: "Other…", exact: true }).first()],
    hidden: [group.getByText("Decision recorded", { exact: true })],
  });
  expect((await remaining(request)).length).toBe(3);
  expect((await read(request)).messages.some((m: any) => m.text.includes("Any retention period is possible."))).toBe(true);
});

test("refused and stale submissions retain independent drafts without accepting replacement wording", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const initial = await read(request);
  const [retention, , recipient] = initial.question_group.questions;
  await walk.open(taskPath);
  const group = page.locator(`[data-group-id="${initial.question_group.id}"]`);
  const card = group.locator(`[data-question-id="${retention.id}"]`);
  await card.getByRole("button", { name: "Other…", exact: true }).click();
  await card.getByRole("textbox").fill("21 days");
  await page.route("**/api/decide", route => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "Fixture transport refusal" }) }));
  await group.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await walk.state("refused-answer-remains-editable", { visible: [card.getByRole("textbox"), group.getByRole("button", { name: "Retry", exact: true })], hidden: [group.getByText("Sent to L2", { exact: true })] });
  await expect(card.getByRole("textbox")).toHaveValue("21 days");
  await page.unroute("**/api/decide");
  const recipientField = group.locator(`[data-question-id="${recipient.id}"]`).getByRole("textbox");
  await recipientField.fill("Release team");
  await page.route("**/api/task/atlas/choose-rollout-settings", route => route.fulfill({ contentType: "application/json", body: JSON.stringify(initial) }));
  expect((await request.post("/fixture/revise")).ok()).toBe(true);
  await group.getByRole("button", { name: "Send 2 answers", exact: true }).click();
  await expect(group.getByRole("alert")).toContainText("changed");
  expect((await remaining(request)).length).toBe(3);
  await page.unroute("**/api/task/atlas/choose-rollout-settings");
  await group.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(group.getByText("How long after migration should we keep the old index?", { exact: true })).toBeVisible();
  await expect(recipientField).toHaveValue("Release team");
  await walk.state("revised-member-clears-only-its-stale-draft", {
    visible: [recipientField, group.getByRole("button", { name: "Send 1 answer", exact: true })],
    hidden: [group.locator(`[data-question-id="${retention.id}"] textarea`)],
  });
});
