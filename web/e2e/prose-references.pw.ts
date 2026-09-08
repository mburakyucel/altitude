import { expect, type Locator } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// Content comes from real saved records and GET /api/project metadata in a disposable home.
// Only named metadata overlays and the external destination are intercepted.
test.use({ serviceScript: "prose-references-service.py" });
test.setTimeout(60_000);
const repository = "https://github.com/example/project";

async function savedReferences(scope: Locator, hasRepository = true) {
  for (const [label, path] of [["PR #250", "pull/250"], ["issue #247", "issues/247"], ["#248", "issues/248"]]) {
    const link = scope.getByRole("link", { name: label, exact: true });
    if (hasRepository) await expect(link).toHaveAttribute("href", `${repository}/${path}`);
    else await expect(link).toHaveCount(0);
  }
  await expect(scope.getByRole("link", { name: "other/repo#12", exact: true })).toHaveAttribute("href", "https://github.com/other/repo/issues/12");
  await expect(scope.getByRole("link", { name: "review", exact: true })).toHaveAttribute("href", `${repository}/pull/251`);
  await expect(scope.getByRole("link", { name: `${repository}/issues/252`, exact: true })).toHaveAttribute("href", `${repository}/issues/252`);
  await expect(scope.locator("a a")).toHaveCount(0);
  await expect(scope.getByRole("link", { name: /#90[01]/ })).toHaveCount(0);
  await expect(scope.locator("code").filter({ hasText: "PR #900" })).toBeVisible();
  await expect(scope.locator("pre").filter({ hasText: "issue #901" })).toBeVisible();
}

test("saved L3 and L2 prose preserves links and code across reload and external activation", async ({ page, context, service }, info) => {
  const walk = walkthrough(page, info);
  const l3 = page.getByRole("region", { name: "Conversation", exact: true });
  const prose = l3.locator(".reply");
  await walk.open(`${service}/projects/alpha`);
  await savedReferences(prose);
  await walk.state("01-saved-l3-references-links-code", {
    visible: [prose, l3.getByRole("link", { name: "PR #260", exact: true })],
    hidden: [l3.getByRole("link", { name: "issue #901", exact: true })],
  });
  await page.reload();
  await savedReferences(prose);
  await walk.state("02-reloaded-saved-l3", { visible: [prose], hidden: [l3.getByLabel("Loading", { exact: true })] });
  const pr = prose.getByRole("link", { name: "PR #250", exact: true });
  await pr.hover();
  await walk.state("03-reference-hover", { visible: [pr], hidden: [] });
  await pr.focus();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Shift+Tab");
  await expect(pr).toBeFocused();
  await expect(pr).toHaveAttribute("target", "_blank");
  await expect(pr).toHaveAttribute("rel", "noopener noreferrer");
  await walk.state("04-reference-keyboard-focus", { visible: [pr], hidden: [] });
  await context.route(`${repository}/pull/250`, (route) => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Fictional PR</title><h1>Fictional PR 250</h1>" }));
  const popupPromise = page.waitForEvent("popup");
  if (info.project.name === "phone") await pr.tap();
  else await page.keyboard.press("Enter");
  const popup = await popupPromise;
  await expect(popup).toHaveURL(`${repository}/pull/250`);
  await expect(popup.getByRole("heading", { name: "Fictional PR 250" })).toBeVisible();
  await expect(page).toHaveURL(`${service}/projects/alpha`);
  await walkthrough(popup, info).state("05-new-tab-destination-overlay", { visible: [popup.getByRole("heading")], hidden: [] });
  await popup.close();
  await l3.getByRole("button", { name: "Show", exact: true }).click();
  await walk.state("06-expanded-system-references", { visible: [l3.getByRole("article", { name: /Report landed/ }), l3.getByRole("link", { name: "Full report", exact: true })], hidden: [l3.getByRole("button", { name: "Show", exact: true })] });
  await walk.open(`${service}/projects/alpha/tasks/reference-task`);
  const l2 = page.getByRole("region", { name: "Task conversation", exact: true });
  const answer = l2.locator('.msg-row[data-role="l2"] .reply');
  await savedReferences(answer);
  await walk.state("07-saved-l2-references-links-code", { visible: [answer, l2.getByRole("link", { name: "pull request #253", exact: true })], hidden: [l2.getByRole("link", { name: "issue #901", exact: true })] });
  await page.reload();
  await savedReferences(answer);
  await walk.state("08-reloaded-saved-l2", { visible: [answer], hidden: [l3] });
});

test("missing, pending and failed repository metadata keep prose readable without invented links", async ({ page, service }, info) => {
  const walk = walkthrough(page, info);
  const l3 = page.getByRole("region", { name: "Conversation", exact: true });
  const prose = l3.locator(".reply");
  await walk.open(`${service}/projects/beta`);
  await savedReferences(prose, false);
  await walk.state("01-missing-repository", { visible: [prose, prose.getByRole("link", { name: "other/repo#12", exact: true })], hidden: [prose.getByRole("link", { name: "PR #250", exact: true })] });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route((url) => url.pathname === "/api/project/alpha", async (route) => { await gate; await route.continue(); }, { times: 1 });
  await walk.open(`${service}/projects/alpha`);
  await savedReferences(prose, false);
  await walk.state("02-pending-metadata-overlay", { visible: [prose], hidden: [prose.getByRole("link", { name: "PR #250", exact: true })] });
  release();
  await savedReferences(prose);
  await walk.state("03-metadata-arrived", { visible: [prose.getByRole("link", { name: "PR #250", exact: true })], hidden: [] });
  await page.route((url) => url.pathname === "/api/project/alpha", (route) => route.fulfill({ status: 503, json: { error: "Controlled metadata failure" } }));
  await page.reload();
  await savedReferences(prose, false);
  const error = page.locator(".project-header").getByText("Controlled metadata failure", { exact: true });
  await expect(error).toBeVisible({ timeout: 15_000 });
  await walk.state("04-failed-metadata-overlay", { visible: [prose, error], hidden: [prose.getByRole("link", { name: "PR #250", exact: true })] });
});

test("decision cards, anchored L2 discussion and report fields share repository reference rendering", async ({ page, service }, info) => {
  const walk = walkthrough(page, info);
  await walk.open(`${service}/`);
  const card = page.getByRole("article", { name: "Reference decision", exact: true });
  const pullLinks = card.getByRole("link", { name: "PR #250", exact: true });
  await expect(pullLinks).toHaveCount(2); // Question and explicit recommendation share the renderer.
  for (const link of await pullLinks.all()) await expect(link).toHaveAttribute("href", `${repository}/pull/250`);
  await expect(card.getByRole("link", { name: "PR #272", exact: true })).toHaveCount(0);
  await expect(card.getByRole("link", { name: "issue #904", exact: true })).toHaveCount(0);
  await walk.state("01-decision-card-and-saved-answer", { visible: [card], hidden: [card.getByRole("link", { name: "issue #904", exact: true })] });
  await card.getByRole("link", { name: "Open L2 chat", exact: true }).click();
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const title = conversation.locator('[data-status="open"]');
  await expect(title.getByRole("link", { name: "issue #247", exact: true })).toHaveAttribute("href", `${repository}/issues/247`);
  await walk.state("02-decision-question", { visible: [title], hidden: [page.getByLabel("Loading", { exact: true })] });
  await expect(conversation.getByRole("link", { name: "other/repo#273", exact: true })).toHaveAttribute("href", "https://github.com/other/repo/issues/273");
  const answerLink = conversation.getByRole("link", { name: "PR #272", exact: true });
  await answerLink.scrollIntoViewIfNeeded();
  await walk.state("03-decision-discussion-answer", { visible: [answerLink], hidden: [conversation.getByRole("link", { name: "issue #904", exact: true })] });
  await walk.open(`${service}/projects/alpha/tasks/reference-task/report`);
  const report = page.locator(".report-page");
  for (const [label, href] of [["PR #260", `${repository}/pull/260`], ["issue #261", `${repository}/issues/261`], ["PR #262", `${repository}/pull/262`], ["other/repo#263", "https://github.com/other/repo/issues/263"], ["issue #264", `${repository}/issues/264`], ["#265", `${repository}/issues/265`], ["PR #270", `${repository}/pull/270`], ["issue #271", `${repository}/issues/271`]]) {
    await expect(report.getByRole("link", { name: label, exact: true })).toHaveAttribute("href", href);
  }
  await walk.state("04-report-landed-review-blocked", { visible: [report.getByRole("region", { name: "Landed", exact: true }), report.getByRole("region", { name: "Review", exact: true })], hidden: [report.getByRole("link", { name: /#90[23]/ })] });
  await report.getByRole("region", { name: "Digest", exact: true }).scrollIntoViewIfNeeded();
  await walk.state("05-report-notes-digest-code", { visible: [report.getByRole("region", { name: "Report notes", exact: true }), report.getByRole("region", { name: "Digest", exact: true })], hidden: [report.getByRole("link", { name: /#90[23]/ })] });
});
