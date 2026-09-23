import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "review-service.py" });

test("operator review captures a real checkpoint, preserves authority and reflects L2 dispositions", async ({ page, request }, info) => {
  test.setTimeout(60_000);
  const initial = await (await request.get("/fixture/status")).json();
  const walk = walkthrough(page, info);
  const menu = page.getByRole("dialog", { name: "Task details" });
  const status = async () => (await request.get("/fixture/status")).json();
  const fixture = async (action: string, data = {}) => {
    const response = await request.post(`/fixture/${action}`, { data });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  await walk.open(`/projects/${initial.project}/tasks/${initial.slug}`);
  await page.getByRole("textbox", { name: "Message the L2" }).fill("Keep this unsent note.");
  await page.getByRole("button", { name: "Task details", exact: true }).click();
  await menu.getByRole("button", { name: "Request cross-engine review" }).click();
  await expect(menu).toBeHidden();
  const record = await status();
  expect(record.review.history).toHaveLength(1);
  expect(record.calls).toBe(0);
  expect(record.pending.some((entry: { review_id?: string }) => entry.review_id === record.review.latest.id)).toBe(true);
  const row = page.locator(`[data-review-id="${record.review.latest.id}"]`);
  await walk.state("01-request-saved-and-pending", { visible: [row.getByText("You requested a cross-engine review · waiting for L2")], hidden: [] });
  const forbidden = await request.post("/fixture/l2-withdraw", { data: {} });
  expect(forbidden.status()).toBe(409);
  expect((await forbidden.json()).error).toContain("Only the operator");
  const running = fixture("run", { hold: true });
  try {
    await walk.state("02-review-running", { visible: [row.getByText("Second engine is reviewing")], hidden: [] });
  } finally { await fixture("release"); }
  const completed = await running;
  expect(completed.state).toBe("completed");
  expect(completed.snapshot.head).toMatch(/^[0-9a-f]{40}$/);
  expect(completed.snapshot.context_ids).toHaveLength(1);
  expect((await status()).calls).toBe(1);
  expect((await status()).hold_merge).toBe(initial.hold_merge);
  await walk.state("03-current-review-completed", { visible: [row.getByText("Second engine review complete · 1 finding · awaiting L2"), row.getByText("Review covers the current revision.")], hidden: [] });
  await row.locator("summary").click();
  await expect(row.getByText("Awaiting L2’s response.")).toBeVisible();
  await fixture("edit");
  await expect(row.getByText(/Work changed after review/)).toBeVisible();
  await row.locator("summary").click();
  await walk.state("04-stale-coverage-visible-collapsed", { visible: [row.getByText(/Work changed after review/)], hidden: [row.getByText("high · Expired cursors restart pagination")] });
  await fixture("assess");
  await expect(row.getByText("Reviewed an earlier revision; L2 assessed the later edits.")).toBeVisible();
  await row.locator("summary").click();
  await row.getByText("high · Expired cursors restart pagination").scrollIntoViewIfNeeded();
  await walk.state("05-owner-disposition", { visible: [row.getByText("Added an explicit expiration response and regression test.")], hidden: [row.getByText("Awaiting L2’s response.")] });
  await expect(page.getByRole("textbox", { name: "Message the L2" })).toHaveValue("Keep this unsent note.");
  await page.getByRole("button", { name: "Task details", exact: true }).click();
  await walk.state("06-existing-result-in-menu", { visible: [menu.getByRole("button", { name: "View review" })], hidden: [menu.getByRole("button", { name: "Request cross-engine review" })] });
  expect((await status()).calls).toBe(1);
});

test("L2 initiates review; failure retry and unavailable second engine use real storage", async ({ page, request }, info) => {
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
  await page.getByRole("button", { name: "Task details", exact: true }).click();
  const menu = page.getByRole("dialog", { name: "Task details" });
  await walk.state("01-second-engine-unavailable", { visible: [menu.getByText("A second engine is unavailable.")], hidden: [page.locator(".task-review-row")] });
  await expect(menu.getByRole("button", { name: "Request cross-engine review" })).toBeDisabled();
  const refused = await request.post("/api/task/review", { data: { project: initial.project, slug: initial.slug, action: "request", request_id: "unavailable-request" } });
  expect(refused.status()).toBe(409);
  expect((await (await request.get("/fixture/status")).json()).review.history).toHaveLength(0);
  await action("mode", { available: true, fail: true });
  const review = await action("l2-request");
  await menu.getByRole("button", { name: "View review" }).click();
  const row = page.locator(`[data-review-id="${review.id}"]`);
  await walk.state("02-l2-elected-review", { visible: [row.getByText("L2 requested a cross-engine review · waiting for L2")], hidden: [] });
  await action("run");
  await row.locator("summary").click();
  await walk.state("03-real-failure", { visible: [row.getByText("Review reached its ten-minute limit."), row.getByRole("button", { name: "Retry review" })], hidden: [] });
  await action("mode", { fail: false });
  const [receipt] = await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === "/api/task/review"),
    row.getByRole("button", { name: "Retry review" }).click(),
  ]);
  expect(receipt.ok(), await receipt.text()).toBe(true);
  const renewed = await (await request.get("/fixture/status")).json();
  const retried = page.locator(`[data-review-id="${renewed.review.latest.id}"]`);
  expect(renewed.review.history).toHaveLength(2);
  await action("run");
  await walk.state("04-retry-completed", { visible: [retried.getByText("Second engine review complete · 1 finding")], hidden: [] });
  expect((await (await request.get("/fixture/status")).json()).calls).toBe(2);
  await page.getByRole("button", { name: "Task details", exact: true }).click();
  await walk.state("05-already-completed-reflected", { visible: [menu.getByRole("button", { name: "View review" })], hidden: [menu.getByRole("button", { name: "Request cross-engine review" })] });
});
