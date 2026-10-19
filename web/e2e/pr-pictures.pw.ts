import { expect, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "pr-pictures-service.py" });

// SPEC.md §3.8.6: a held PR's merge card shows the pictures its commit adds, replaces or removes.
const card = (page: Page, title: string) => page.getByRole("article", { name: title, exact: true });
const pictures = (scope: Locator) => scope.getByRole("region", { name: "Pictures in this PR", exact: true });
const figure = (scope: Locator, path: string) => pictures(scope).locator("figure").filter({ hasText: path });
const settled = (scope: Locator) => expect(scope.locator("img[data-loading]")).toHaveCount(0);

test("the generated merge card shows the PR's pictures with before and after, and a text-only PR shows none", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/");
  const readme = card(page, "Refresh the README pictures");
  const overview = figure(readme, "docs/overview.png");
  await settled(pictures(readme));
  await pictures(readme).scrollIntoViewIfNeeded();
  await walk.state("01-merge-card-pictures", {
    visible: [pictures(readme).getByText("Pictures in this PR", { exact: true }), figure(readme, "docs/banner.png").getByText("Removed", { exact: true }),
      overview.getByRole("button", { name: "After", pressed: true }), figure(readme, "docs/phone.png"),
      readme.getByRole("button", { name: "Show 1 more picture", exact: true }),
      readme.getByText("Also changed: docs/logo.svg", { exact: true }), readme.getByRole("button", { name: "Approve merge" })],
    hidden: [figure(readme, "docs/review.webp")],
  });
  // The pictures sit between the card's text and its review box, and never widen the card.
  const box = (await pictures(readme).boundingBox())!;
  const outer = (await readme.boundingBox())!;
  expect(box.x + box.width).toBeLessThanOrEqual(outer.x + outer.width);
  expect(box.y + box.height).toBeLessThanOrEqual((await readme.locator(".question-review").boundingBox())!.y);
  const after = await overview.locator("img").getAttribute("src");

  await overview.getByRole("button", { name: "Before", exact: true }).click();
  await settled(overview);
  await overview.scrollIntoViewIfNeeded();
  await walk.state("02-before", {
    visible: [overview.getByRole("button", { name: "Before", pressed: true }), overview.getByRole("img", { name: "overview.png, before" })],
    hidden: [overview.getByRole("img", { name: "overview.png, after" })],
  });
  expect(await overview.locator("img").getAttribute("src")).not.toBe(after);

  await readme.getByRole("button", { name: "Show 1 more picture", exact: true }).click();
  await settled(pictures(readme));
  await figure(readme, "docs/review.webp").scrollIntoViewIfNeeded();
  await walk.state("03-all-pictures", {
    visible: [figure(readme, "docs/review.webp").getByText("New", { exact: true })],
    hidden: [readme.getByRole("button", { name: /more picture/ })],
  });

  // A picture opens the gallery at the side it shows; Back closes it to the card.
  await overview.getByRole("button", { name: "Open image overview.png" }).click();
  const viewer = page.getByRole("dialog");
  await walk.state("04-gallery", { visible: [viewer, viewer.getByText("2 / 4")], hidden: [] });
  await page.goBack();
  await expect(viewer).toBeHidden();
  await expect(overview.getByRole("button", { name: "Before", pressed: true })).toBeVisible();

  const timer = card(page, "Fix the timer copy");
  await timer.scrollIntoViewIfNeeded();
  await walk.state("05-text-only-pr", {
    visible: [timer.getByRole("button", { name: "Approve merge" })],
    hidden: [pictures(timer)],
  });
});

test("the owner's merge question shows its own files beside the PR's pictures in Needs you and the chat", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/");
  const help = card(page, "Polish the help page");
  await settled(pictures(help));
  await pictures(help).scrollIntoViewIfNeeded();
  await walk.state("06-merge-question-needs-you", {
    visible: [help.getByRole("button", { name: "Open image composed.png" }), figure(help, "docs/help.png").getByText("New", { exact: true }),
      help.locator(".question-review").getByText("Implementation review"), help.getByRole("button", { name: /Approve merge/ })],
    hidden: [help.getByRole("button", { name: /more picture/ })],
  });
  await help.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  const question = page.getByRole("region", { name: "Task conversation", exact: true }).locator(".conversation-question");
  await settled(pictures(question));
  await pictures(question).scrollIntoViewIfNeeded();
  await walk.state("07-merge-question-chat", {
    visible: [question.getByRole("button", { name: "Open image composed.png" }), figure(question, "docs/help.png"),
      question.getByRole("link", { name: "View PR #52", exact: true })],
    hidden: [],
  });
});

test("pictures load in place, fail with Retry, and a card whose copies are gone says so", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/shown/atlas/*/pr/51/files/*", async (route) => { await gate; await route.continue(); });
  await walk.open("/");
  const readme = card(page, "Refresh the README pictures");
  await pictures(readme).scrollIntoViewIfNeeded();
  await walk.state("08-loading", { visible: [figure(readme, "docs/phone.png").locator(".pr-picture-frame:has(img[data-loading])")], hidden: [] });
  release();
  await settled(pictures(readme));
  await page.unroute("**/api/shown/atlas/*/pr/51/files/*");

  await page.route("**/api/shown/atlas/*/pr/51/files/*", (route) => route.fulfill({ status: 404, body: "Unavailable" }));
  const phone = figure(readme, "docs/phone.png");
  await phone.getByRole("button", { name: "Before", exact: true }).click();
  await phone.scrollIntoViewIfNeeded();
  await walk.state("09-failed", {
    visible: [phone.getByText("Image unavailable", { exact: true }), phone.getByRole("button", { name: "Retry", exact: true })],
    hidden: [phone.getByRole("img")],
  });
  await page.unroute("**/api/shown/atlas/*/pr/51/files/*");
  await phone.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(phone.getByRole("img", { name: "phone.png, before" })).toBeVisible();

  // Copies gone or resized since the card was read: one closed card, the merge controls unchanged.
  await page.route("**/api/overview", async (route) => {
    const response = await route.fetch();
    const body = JSON.stringify(await response.json()).replaceAll('"available":true,"pictures"', '"available":false,"pictures"');
    await route.fulfill({ response, body, headers: { ...response.headers(), "content-type": "application/json" } });
  });
  await page.reload();
  await readme.locator(".shown-card[data-gone]").scrollIntoViewIfNeeded();
  await walk.state("10-unavailable", {
    visible: [readme.locator(".shown-card[data-gone]").getByText("Preview unavailable", { exact: true }),
      readme.getByRole("button", { name: "Approve merge" })],
    hidden: [pictures(readme)],
  });
});
