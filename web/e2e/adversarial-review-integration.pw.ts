import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "review-service.py" });

const verdict = "Expired cursors restart pagination instead of returning the agreed error.";
const detailsButton = /^(?:Keep pagination stable — )?Task details$/;

test("proposal review preserves its approval question and captured version alongside later changes", async ({ page, request }, info) => {
  test.setTimeout(60_000);
  const status = async () => (await request.get("/fixture/status")).json();
  const fixture = async (name: string) => {
    const response = await request.post(`/fixture/${name}`, { data: {} });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  await request.post("/fixture/mode", { data: { same_engine: true } });
  await fixture("proposal-question");
  const initial = await status();
  const walk = walkthrough(page, info);
  await walk.open(`/projects/${initial.project}/tasks/${initial.slug}`);
  const menu = page.getByRole("dialog", { name: "Task details" });
  const proposalBox = menu.getByRole("region", { name: "Proposal review" });
  const changesBox = menu.getByRole("region", { name: "Implementation review" });
  await page.getByRole("button", { name: detailsButton }).click();
  await expect(changesBox.getByRole("button", { name: "Request" })).toBeEnabled();
  await walk.state("01-proposal-request-before-approval", { visible: [proposalBox.getByRole("button", { name: "Request" }), proposalBox.getByText("Not reviewed yet")], hidden: [] });
  await proposalBox.getByRole("button", { name: "Request" }).click();
  await expect(menu).toBeHidden();
  const requested = await status();
  expect(requested.review.history.at(-1).subject).toBe("proposal");
  expect(requested.questions).toEqual(initial.questions);
  expect(requested.calls).toBe(0);
  const completed = await fixture("run");
  expect(completed.snapshot.proposal).toEqual({ id: initial.proposal.id, at: initial.proposal.at, text: initial.proposal.text });
  expect(completed.same_engine).toBe(true);
  const card = page.locator(`[data-review-id="${completed.id}"]`);
  await page.getByRole("button", { name: detailsButton }).click();
  await expect(proposalBox.getByText("L2 is responding")).toBeVisible();
  await proposalBox.getByRole("button", { name: "View", exact: true }).click();
  await card.getByRole("button", { name: "Technical details" }).click();
  await expect(card.getByText(initial.proposal.text, { exact: true })).toBeVisible();
  await walk.state("02-proposal-result-before-approval", { visible: [card.getByText(verdict), card.getByText("Owner engine · same engine as the task"), page.getByText("Use this pagination approach?", { exact: true })], hidden: [] });
  await fixture("revise-proposal");
  // The revised message changes the reviewed context, not committed content: L2 reassesses it.
  expect((await status()).review.history.at(-1)).toMatchObject({ coverage: "earlier", earlier: false });
  await fixture("assess");
  expect((await status()).questions).toEqual(initial.questions);
  await walk.state("03-revised-proposal-assessed", { visible: [card.getByText("1 finding, resolved"), card.getByText(initial.proposal.text, { exact: true })], hidden: [card.getByText(/earlier version/)] });
  await fixture("approve-proposal");
  await fixture("assess");
  await page.getByRole("button", { name: detailsButton }).click();
  await changesBox.getByRole("button", { name: "Request" }).click();
  await expect(menu).toBeHidden();
  await fixture("run");
  await fixture("assess");
  const both = await status();
  expect(both.review.history).toHaveLength(2);
  expect(both.review.subjects.proposal.latest.id).toBe(completed.id);
  expect(both.review.subjects.changes.latest.subject).toBe("changes");
  expect(both.calls).toBe(2);
  expect(both.hold_merge).toBe(initial.hold_merge);
  await page.getByRole("button", { name: detailsButton }).click();
  await walk.state("04-both-subject-results-retained", { visible: [proposalBox.getByRole("button", { name: "View", exact: true }), changesBox.getByRole("button", { name: "View", exact: true }), changesBox.getByText("1 finding, resolved")], hidden: [proposalBox.getByRole("button", { name: "Request" }), changesBox.getByRole("button", { name: "Request" })] });
  await proposalBox.getByRole("button", { name: "View", exact: true }).click();
  await expect(card.getByText(verdict)).toBeVisible();
  expect((await status()).calls).toBe(2);
});

test("operator review captures a real checkpoint, preserves authority and reflects L2 dispositions", async ({ page, request }, info) => {
  test.setTimeout(60_000);
  const initial = await (await request.get("/fixture/status")).json();
  const walk = walkthrough(page, info);
  const menu = page.getByRole("dialog", { name: "Task details" });
  const changesBox = menu.getByRole("region", { name: "Implementation review" });
  const status = async () => (await request.get("/fixture/status")).json();
  const fixture = async (action: string, data = {}) => {
    const response = await request.post(`/fixture/${action}`, { data });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  await walk.open(`/projects/${initial.project}/tasks/${initial.slug}`);
  await page.getByRole("textbox", { name: "Message the L2" }).fill("Keep this unsent note.");
  await page.getByRole("button", { name: detailsButton }).click();
  await changesBox.getByRole("button", { name: "Request" }).click();
  await expect(menu).toBeHidden();
  const record = await status();
  const latest = record.review.history.at(-1);
  expect(record.review.history).toHaveLength(1);
  expect(record.calls).toBe(0);
  expect(record.pending.some((entry: { review_id?: string }) => entry.review_id === latest.id)).toBe(true);
  const card = page.locator(`[data-review-id="${latest.id}"]`);
  const toggle = card.getByRole("button", { name: /implementation review details$/ });
  await walk.state("01-request-saved-and-queued", { visible: [card.getByText("· requested by you"), card.getByText(/^Queued\. L2 starts it/)], hidden: [] });
  const forbidden = await request.post("/fixture/l2-withdraw", { data: {} });
  expect(forbidden.status()).toBe(409);
  expect((await forbidden.json()).error).toContain("Only the operator");
  const running = fixture("run", { hold: true });
  try {
    await walk.state("02-review-running", { visible: [card.getByText("· in progress"), card.getByText("Reviewing the implementation…"), card.getByRole("button", { name: "Stop" })], hidden: [card.getByText("· requested by you")] });
  } finally { await fixture("release"); }
  const completed = await running;
  expect(completed.state).toBe("completed");
  expect(completed.snapshot.head).toMatch(/^[0-9a-f]{40}$/);
  expect(completed.snapshot.context_ids).toHaveLength(1);
  expect((await status()).calls).toBe(1);
  expect((await status()).hold_merge).toBe(initial.hold_merge);
  await walk.state("03-current-review-completed", { visible: [card.getByText(verdict), card.getByText("L2 is responding")], hidden: [card.getByRole("button", { name: "Stop" })] });
  await toggle.click();
  await expect(card.getByText("Expired cursors restart pagination", { exact: true })).toBeVisible();
  await expect(card.getByText("New", { exact: true })).toBeVisible();
  await fixture("edit");
  await expect(card.getByText(/earlier version/)).toBeVisible();
  await toggle.click();
  await walk.state("04-earlier-version-collapsed", { visible: [card.getByText(/earlier version/)], hidden: [card.getByText("Expired cursors restart pagination", { exact: true })] });
  await fixture("assess");
  await expect(card.getByText("1 finding, resolved")).toBeVisible();
  await toggle.click();
  await walk.state("05-owner-disposition", { visible: [card.getByText("Added an explicit expiration response and regression test."), card.getByText("Fixed", { exact: true })], hidden: [card.getByText("New", { exact: true }), card.getByText(/earlier version/)] });
  await expect(page.getByRole("textbox", { name: "Message the L2" })).toHaveValue("Keep this unsent note.");
  await page.getByRole("button", { name: detailsButton }).click();
  await walk.state("06-existing-result-in-menu", { visible: [changesBox.getByRole("button", { name: "View", exact: true }), changesBox.getByRole("button", { name: "Review again" })], hidden: [changesBox.getByRole("button", { name: "Request" })] });
  expect((await status()).calls).toBe(1);
});

test("L2 initiates review; failure, try again and an unavailable reviewer use real storage", async ({ page, request }, info) => {
  test.setTimeout(60_000);
  const initial = await (await request.get("/fixture/status")).json();
  const walk = walkthrough(page, info);
  const action = async (name: string, data = {}) => {
    const response = await request.post(`/fixture/${name}`, { data });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  await action("mode", { available: false });
  await walk.open(`/projects/${initial.project}/tasks/${initial.slug}`);
  await page.getByRole("button", { name: detailsButton }).click();
  const menu = page.getByRole("dialog", { name: "Task details" });
  const changesBox = menu.getByRole("region", { name: "Implementation review" });
  await walk.state("01-reviewer-unavailable", { visible: [changesBox.getByText("No reviewer is available.")], hidden: [changesBox.getByRole("button"), page.locator(".review-card")] });
  const refused = await request.post("/api/task/review", { data: { project: initial.project, slug: initial.slug, action: "request", request_id: "unavailable-request" } });
  expect(refused.status()).toBe(409);
  expect((await (await request.get("/fixture/status")).json()).review.history).toHaveLength(0);
  await action("mode", { available: true, fail: true });
  const review = await action("l2-request");
  await changesBox.getByRole("button", { name: "View", exact: true }).click();
  const card = page.locator(`[data-review-id="${review.id}"]`);
  await walk.state("02-l2-requested-review", { visible: [card.getByText("· requested by L2"), card.getByText("L2 asked for a review of its implementation and starts it shortly.")], hidden: [] });
  await action("run");
  await walk.state("03-real-failure", { visible: [card.getByText("The review engine exited before returning findings."), card.getByRole("button", { name: "Try again" })], hidden: [] });
  await action("mode", { fail: false });
  const [receipt] = await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === "/api/task/review"),
    card.getByRole("button", { name: "Try again" }).click(),
  ]);
  expect(receipt.ok(), await receipt.text()).toBe(true);
  const renewed = await (await request.get("/fixture/status")).json();
  const retried = page.locator(`[data-review-id="${renewed.review.history.at(-1).id}"]`);
  expect(renewed.review.history).toHaveLength(2);
  await action("run");
  await walk.state("04-try-again-completed", { visible: [retried.getByText(verdict), retried.getByText(/Review 2/)], hidden: [card] });
  await retried.getByRole("button", { name: "Show implementation review details" }).click();
  await expect(retried.getByText("Earlier reviews")).toBeVisible();
  await expect(retried.getByText(/The review engine exited before returning findings/)).toBeVisible();
  expect((await (await request.get("/fixture/status")).json()).calls).toBe(2);
  await page.getByRole("button", { name: detailsButton }).click();
  await walk.state("05-unassessed-result-in-menu", { visible: [changesBox.getByRole("button", { name: "View", exact: true }), changesBox.getByText("L2 is responding")], hidden: [changesBox.getByRole("button", { name: "Review again" }), changesBox.getByRole("button", { name: "Request" })] });
});
