import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Improvement suggestions in the project chat (design/wireframes/improvement-review/PROPOSAL.md, rollout step 2): the
 * review's endings and its posted message are plain replies written by Altitude through the real run lifecycle. The
 * kept numbers stay as posted, each cited task opens in place, and Back returns to the conversation.
 */
test.use({ serviceScript: "improvement-suggestions-service.py" });

test("posted suggestions read as one reply with run numbers, task links and drops; endings read as one line", async ({ page }, info) => {
  test.setTimeout(60_000);
  const walk = walkthrough(page, info);
  const chat = page.getByRole("region", { name: "Conversation", exact: true });
  const heading = chat.getByText(/^Improvement suggestions of \w{3} \d+ · 5 tasks from \w{3} \d+/);
  const post = heading.locator("xpath=ancestor::div[contains(@class, 'turn')][1]");
  const first = post.getByText("1. Owners re-ask merge questions you already answered", { exact: true });
  const third = post.getByText("3. Reports name checks that never ran", { exact: true });
  const dropped = post.getByText("Dropped 2: an open task already adds that target", { exact: true });
  const failed = chat.getByText("Improvement suggestions failed · it went over its usage limit.", { exact: true });
  const nothing = chat.getByText(/^Improvement suggestions of \w{3} \d+ · 5 tasks from .+ · nothing to suggest\.$/);
  const harbor = post.getByText(/harbor list is rebuilt/);

  await walk.open("/projects/alpha");
  await walk.state("01-endings-and-posted-suggestions", { visible: [failed, nothing, heading, first, third, dropped], hidden: [harbor] });
  await expect(post.locator("ol, ul")).toHaveCount(0);
  await expect(post.locator("strong")).toHaveText(["1. Owners re-ask merge questions you already answered", "3. Reports name checks that never ran"]);
  await expect(post.getByText(/· How agents work$/)).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  const links = post.getByRole("link");
  expect(await links.count()).toBeGreaterThanOrEqual(2);
  const cited = links.first();
  const title = (await cited.textContent())!;
  await expect(cited).toHaveAttribute("href", /^\/projects\/alpha\/tasks\/dock-\d+$/);
  await expect(cited).not.toHaveAttribute("target", /.*/);
  await expect(cited).toHaveCSS("text-decoration-line", "underline");
  await cited.scrollIntoViewIfNeeded();
  if (info.project.name === "phone") await cited.tap();
  else await cited.click();
  await expect(page).toHaveURL(/\/projects\/alpha\/tasks\/dock-\d+$/);
  const task = page.getByRole("heading", { name: title, exact: true }).first();
  await walk.state("02-cited-task-opens-in-place", { visible: [task], hidden: [heading] });

  await page.goBack();
  await expect(page).toHaveURL(/\/projects\/alpha$/);
  await walk.state("03-back-returns-to-the-conversation", { visible: [heading, first, dropped], hidden: [] });
});
