import { expect, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "review-status-service.py" });

/** The router's index of the shown history entry: Back from the review must land on the entry it left. */
const entry = (page: Page) => page.evaluate(() => (window.history.state as { idx: number }).idx);
const conversation = (page: Page) => page.getByRole("region", { name: "Task conversation", exact: true });
const proposal = "Proposal review · done, 2 findings resolved";
const implementation = "Implementation review · done, 1 open finding";

/** Follow a question's review line, check its review card is open and in view, and return with `back`. */
async function roundTrip(page: Page, link: Locator, review: string, finding: string, back: () => Promise<unknown>) {
  const before = await entry(page);
  const url = page.url();
  await link.click();
  await expect(page).toHaveURL(new RegExp(`\\?review=${review}$`));
  const card = page.locator(`#review-${review}`);
  await expect(card.getByText(finding, { exact: true })).toBeInViewport();
  expect(await entry(page)).toBe(before + 1);
  await back();
  await expect(page).toHaveURL(url);
  expect(await entry(page)).toBe(before);
}

test("Needs you shows each proposal and merge review and Back returns to its card", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/");
  const settings = page.getByRole("article", { name: "Settings layout", exact: true });
  const header = page.getByRole("article", { name: "Compact header", exact: true });
  const merge = page.getByRole("article", { name: "Keep pagination stable", exact: true });
  const region = page.getByRole("article", { name: "Backup region", exact: true });
  const notes = page.getByRole("article", { name: "Release notes", exact: true });
  const longest = notes.getByRole("link", { name: "Implementation review · done, 3 findings pending · earlier version", exact: true });
  await walk.state("01-needs-you-review-lines", {
    visible: [settings.getByRole("link", { name: proposal, exact: true }), settings.locator(".question-review .review-icon-ok"),
      header.getByText("Proposal review · not done", { exact: true }), header.locator(".question-review .review-icon-warn"),
      merge.getByRole("link", { name: implementation, exact: true }), merge.locator(".question-review .review-icon-warn"),
      longest, notes.locator(".question-review .review-icon-warn")],
    // No review means no link; a question that is neither a proposal nor a merge approval has no line.
    hidden: [header.getByRole("link", { name: /^Proposal review/ }), region.locator(".question-review")],
  });

  // Each review is its own bordered box inside the card: a full-size target with a chevron when it opens the review.
  const box = (await longest.boundingBox())!;
  expect(box.height).toBeGreaterThanOrEqual(44);
  expect(box.x + box.width).toBeLessThanOrEqual((await notes.boundingBox())!.x + (await notes.boundingBox())!.width);
  await expect(longest).toHaveCSS("border-top-style", "solid");
  await expect(longest.locator(".question-review-go")).toBeVisible();
  await expect(header.locator(".question-review")).toHaveCSS("border-top-style", "dashed");
  await expect(header.locator(".question-review-go")).toHaveCount(0);
  await longest.scrollIntoViewIfNeeded();
  await walk.state("01b-longest-line-and-no-line", { visible: [longest, header.getByText("Proposal review · not done", { exact: true })], hidden: [region.locator(".question-review")] });
  await header.locator(".question-review").scrollIntoViewIfNeeded();
  await walk.state("01c-no-review", { visible: [header.getByText("not done", { exact: true })], hidden: [header.getByRole("link", { name: /^Proposal review/ })] });
  await page.locator(".needs-page").evaluate((node) => { node.scrollTop = 0; });

  await roundTrip(page, merge.getByRole("link", { name: implementation, exact: true }), "keep-pagin-changes",
    "Expired cursors restart pagination", () => page.goBack());
  await walk.state("02-browser-back-to-needs-you", { visible: [merge.getByRole("link", { name: implementation, exact: true })], hidden: [] });
  await expect(merge.getByRole("link", { name: implementation, exact: true })).toBeInViewport();

  // The app's own Back (phone header or desktop crumb) behaves the same.
  await roundTrip(page, settings.getByRole("link", { name: proposal, exact: true }), "settings-l-proposal",
    "Muted labels lack contrast", () => page.getByRole("button", { name: "Back", exact: true }).filter({ visible: true }).first().click());
  await expect(settings.getByRole("link", { name: proposal, exact: true })).toBeInViewport();
  await walk.state("03-app-back-to-needs-you", { visible: [settings.getByRole("link", { name: proposal, exact: true })], hidden: [] });
});

test("a question's review line in its conversation opens the review card and Back returns to the question", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/projects/atlas/tasks/settings-layout");
  const question = conversation(page).locator(".conversation-question").last();
  const link = question.getByRole("link", { name: proposal, exact: true });
  await walk.state("04-conversation-proposal-line", { visible: [link, question.getByRole("link", { name: /^View preview/ })], hidden: [] });
  await expect(link).toBeInViewport();
  await roundTrip(page, link, "settings-l-proposal", "Muted labels lack contrast", async () => {
    await walk.state("05-conversation-review-opened", { visible: [page.locator("#review-settings-l-proposal")], hidden: [] });
    await page.goBack();
  });
  await expect(link).toBeInViewport();
  await walk.state("06-conversation-back-to-question", { visible: [link], hidden: [] });

  await walk.open("/projects/atlas/tasks/keep-pagination-stable");
  const card = conversation(page).locator('[data-review-task="atlas/keep-pagination-stable"]');
  const line = card.getByRole("link", { name: implementation, exact: true });
  await expect(card.getByRole("button", { name: "Approve merge", exact: true })).toBeVisible();
  await walk.state("07-conversation-merge-line", { visible: [line], hidden: [] });
  await roundTrip(page, line, "keep-pagin-changes", "Expired cursors restart pagination",
    () => page.getByRole("button", { name: "Back", exact: true }).filter({ visible: true }).first().click());
  await expect(line).toBeInViewport();
});
