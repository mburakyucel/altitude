import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

const route = "/projects/atlas";
// What altd writes for L3 alone: question ids, revisions, authority words and coordinator instructions.
const coordinatorOnly = /revision \d|authority:|alt task|grants no operator authority/;

test.setTimeout(120_000); // Idle conversations poll every 20 seconds.

test("coordinator notices read as one line each, queued or handled, never as their prompt", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await walk.open(route);
  const convo = page.getByRole("region", { name: "Conversation", exact: true });
  await expect(convo.getByText("The fixture records the chosen scope.")).toBeVisible();
  const response = await request.post("/fixture/queued-notices", { data: {} });
  expect(response.ok()).toBe(true);
  await page.reload();

  const queued = convo.getByRole("list", { name: "Queued messages" });
  const block = queued.locator(".sys-line", { hasText: "Block · Choose validation scope · Use the bounded validation scope, without PR #12?" });
  const report = queued.locator(".sys-line", { hasText: "Report landed · Document search contract · incomplete" });
  const fault = queued.locator(".sys-line", { hasText: "Fault · Prepare index migration" });
  const handled = convo.locator(".sys-line", { hasText: "Asked for the validation scope; the owner keeps the bounded plan meanwhile." });
  await expect(block).toBeVisible({ timeout: 30_000 });
  await block.scrollIntoViewIfNeeded();
  await walk.state("01-queued-notices-folded", {
    visible: [block, report, fault, handled, block.getByRole("link", { name: "Needs you" }), report.getByRole("link", { name: "Open task" })],
    hidden: [convo.getByText(coordinatorOnly), queued.getByRole("button", { name: "Remove" }), queued.getByRole("button", { name: "Show" })],
  });
  await expect(fault.locator(".sys-dot")).toHaveAttribute("data-tone", "danger");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  const card = convo.getByRole("article", { name: "Block · Choose validation scope" });
  await walk.state("02-handled-block-card", {
    action: () => handled.getByRole("button", { name: "Show", exact: true }).click(),
    visible: [card, card.getByText("L3 replied"), card.getByRole("link", { name: "Open task" })],
    hidden: [handled, card.getByText(coordinatorOnly), card.getByText("What altd sent L3")],
  });
  await walk.state("03-handled-block-folded", {
    action: () => card.getByRole("button", { name: "Hide", exact: true }).click(),
    visible: [handled, block], hidden: [card],
  });

  await walk.state("04-needs-you", {
    action: () => block.getByRole("link", { name: "Needs you" }).click(),
    visible: [page.getByText(/Use the bounded validation scope/).first()],
    hidden: [block],
  });
  await expect(page).toHaveURL(/\/projects\/atlas\/decisions\//);
});
