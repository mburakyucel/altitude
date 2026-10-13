import { expect, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "review-status-service.py" });

/** The router's index of the shown history entry: Back from the review must land on the entry it left. */
const entry = (page: Page) => page.evaluate(() => (window.history.state as { idx: number }).idx);
const conversation = (page: Page) => page.getByRole("region", { name: "Task conversation", exact: true });
const proposal = "Proposal review · done, 2 findings resolved";
const implementation = "Implementation review · done, 1 finding resolved";

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
      merge.getByRole("link", { name: implementation, exact: true }), merge.locator(".question-review .review-icon-ok"),
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
  // A proposal without a design preview, whose review its owner judged unnecessary, says so calmly and opens the record.
  const queue = page.getByRole("article", { name: "Queue per project", exact: true });
  const skipped = queue.getByRole("link", { name: "Proposal review · skipped by L2 · Small, reversible change.", exact: true });
  await skipped.scrollIntoViewIfNeeded();
  await expect(skipped.locator(".review-icon-skip")).toBeVisible();
  await walk.state("01d-skipped-by-owner", { visible: [skipped], hidden: [queue.locator(".question-preview")] });
  await roundTrip(page, skipped, await skipped.getAttribute("href").then((href) => new URLSearchParams(href!.split("?")[1]).get("review")!),
    "Small, reversible change.", () => page.goBack());
  await expect(skipped).toBeInViewport();
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
  await walk.state("04-conversation-proposal-line", { visible: [link, question.getByRole("link", { name: "Open proposal.md", exact: true })], hidden: [] });
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

test("a marked merge question that never names its held PR is the one Approve merge control", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const review = "Implementation review · done, 1 finding resolved";
  const viewPr = (scope: Locator) => scope.getByRole("link", { name: "View PR #44", exact: true });
  await walk.open("/");
  const needs = page.getByRole("article", { name: "Help page copy", exact: true });
  await expect(needs).toHaveCount(1);
  await needs.scrollIntoViewIfNeeded();
  await expect(viewPr(needs)).toHaveAttribute("href", "https://github.com/example/atlas/pull/44");
  await walk.state("09-needs-you-one-merge-control", {
    visible: [needs.getByRole("link", { name: review, exact: true }), needs.getByRole("button", { name: /^Approve merge/ }), viewPr(needs)],
    hidden: [page.getByText("Review PR #44 before merge", { exact: true })],
  });

  await walk.open("/projects/atlas/tasks/help-page-copy");
  const chat = conversation(page);
  const question = chat.locator(".conversation-question").last();
  await expect(chat.getByRole("button", { name: /^Approve merge/ })).toHaveCount(1);
  await walk.state("10-conversation-one-merge-control", {
    visible: [question.getByText("Merge the new help page? It is on the pull request with all checks green.", { exact: true }),
      question.getByRole("link", { name: review, exact: true }), viewPr(question)],
    hidden: [chat.getByText("Your turn · review before merge", { exact: true })],
  });

  // Answering keeps one surface: the choice goes to the L2 as the operator's reply and no review card appears.
  await question.getByRole("button", { name: /^Approve merge/ }).click();
  await walk.state("11-conversation-approve-selected", {
    visible: [question.getByRole("button", { name: "Send 1 answer", exact: true }), viewPr(question)], hidden: [],
  });
  await question.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await walk.state("12-conversation-approval-sent", {
    visible: [chat.getByText("Sent · the L2 has your reply.", { exact: true })],
    hidden: [chat.getByText("Your turn · review before merge", { exact: true }), viewPr(chat),
      chat.getByRole("button", { name: /^(Approve merge|Request changes)$/ })],
  });
});

test("Back after the question is revised while its review is open returns to the latest revision", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  // While reading the review the operator messages the L2, which revises the question.
  const revise = async (revision: number) => {
    await expect(page.locator("#review-settings-l-proposal")).toBeVisible();
    await page.getByRole("textbox", { name: "Message the L2", exact: true }).fill("Raise the label contrast.");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    const latest = page.locator("p.decision-question", { hasText: new RegExp(`^Build settings layout revision ${revision} as shown\\?$`) });
    await expect(latest).toBeAttached();
    await page.goBack();
    await expect(latest).toBeInViewport();
    return latest;
  };
  for (const [state, path, revision] of [["needs-you", "/", 2], ["conversation", "/projects/atlas/tasks/settings-layout", 3]] as const) {
    await walk.open(path);
    const scope = path === "/" ? page.getByRole("article", { name: "Settings layout", exact: true }) : conversation(page).locator(".conversation-question").last();
    await scope.getByRole("link", { name: /^Proposal review/ }).click();
    await walk.state(`08-${state}-back-to-revised-question`, { visible: [await revise(revision)], hidden: [] });
  }
  // A conversation opened from Needs you names the question's revision; Back still returns to the latest one.
  await walk.open("/");
  await page.getByRole("article", { name: "Settings layout", exact: true }).getByRole("link", { name: "Open L2 chat", exact: true }).click();
  await expect(page).toHaveURL(/\?question=.+&revision=3$/);
  await conversation(page).locator(".conversation-question").last().getByRole("link", { name: /^Proposal review/ }).click();
  await revise(4);
  await expect(page).toHaveURL(/\?question=.+&revision=3$/);
});
