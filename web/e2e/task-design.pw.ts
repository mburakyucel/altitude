import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "task-design-service.py" });
const slug = "conversation-layout";
const taskPath = `/projects/atlas/tasks/${slug}`;
type Question = { id: string; revision: number; status: string; design_url: string };
async function task(request: APIRequestContext) {
  const response = await request.get(`/api/task/atlas/${slug}`);
  expect(response.ok()).toBe(true);
  return response.json() as Promise<{ question: Question; hold_merge: string; messages: { text: string }[] }>;
}
const atQuestion = (q: Question) => `${taskPath}?question=${q.id}&revision=${q.revision}`;
const card = (page: Page, q: Question) => page.getByRole("region", { name: "Task conversation", exact: true })
  .locator(`[data-question-id="${q.id}"][data-question-revision="${q.revision}"]`);

test("a saved proposal opens from chat, full size and back; follow-up is not approval and approval retains the hold", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const initial = await task(request);
  const q = initial.question;
  await walk.open("/");
  await expect(page.getByRole("heading", { name: "Needs you", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "View proposal", exact: false })).toHaveCount(0);
  await walk.open(atQuestion(q));
  const link = card(page, q).getByRole("link", { name: "View proposal · v1", exact: true });
  await walk.state("01-question-preview-entry", { visible: [link, card(page, q).getByRole("button", { name: "Use this design" })], hidden: [] });
  const popup = page.waitForEvent("popup");
  await link.click();
  const preview = await popup;
  const previewWalk = walkthrough(preview, info);
  await expect(preview).toHaveURL(new RegExp(`${q.design_url}$`));
  await expect(preview.getByRole("img", { name: "Phone conversation", exact: true })).toBeVisible();
  const fullSize = preview.getByRole("link", { name: "Open Phone conversation full size", exact: true });
  await previewWalk.state("02-saved-proposal", {
    visible: [preview.getByRole("main").getByRole("heading", { name: "Conversation layout", exact: true }), fullSize, preview.getByRole("link", { name: "← Back to question", exact: true })],
    hidden: [preview.getByText("Loading proposal…", { exact: true }), preview.getByRole("button", { name: "Use this design" })],
  });
  expect((await task(request)).question.status).toBe("open");
  expect(await preview.evaluate(() => "proposalExecuted" in window)).toBe(false);
  await expect(preview.getByText("<script>window.proposalExecuted = true</script>", { exact: true })).toBeVisible();
  await preview.getByRole("region", { name: "Proposal text", exact: true }).scrollIntoViewIfNeeded();
  await previewWalk.state("02b-readable-proposal-text", { visible: [preview.getByRole("region", { name: "Proposal text", exact: true })], hidden: [preview.locator(".design-text script")] });
  const imagePopup = preview.waitForEvent("popup");
  await fullSize.click();
  const image = await imagePopup;
  await image.waitForLoadState();
  await expect(image.locator("img")).toBeVisible();
  await walkthrough(image, info).state("03-full-size-image", { visible: [image.locator("img")], hidden: [image.locator("script")] });
  await image.close();
  expect((await request.post("/fixture/edit-worktree")).ok()).toBe(true);
  await preview.reload();
  await expect(preview.getByRole("img", { name: "Phone conversation", exact: true })).toBeVisible();
  await expect(preview.getByText("Unpresented worktree edit", { exact: false })).toHaveCount(0);
  await preview.getByRole("link", { name: "← Back to question", exact: true }).click();
  await expect(preview).toHaveURL(new RegExp(`${taskPath}\\?question=${q.id}&revision=1$`));
  await expect(card(preview, q)).toBeInViewport();
  await preview.getByRole("textbox", { name: "Message the L2", exact: true }).fill("Could the reply have more room?");
  await preview.getByRole("region", { name: "Task conversation", exact: true }).getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(async () => (await task(request)).messages.some((m) => m.text === "Could the reply have more room?")).toBe(true);
  expect((await task(request)).question.status).toBe("open");
  await previewWalk.state("04-follow-up-keeps-decision-open", {
    visible: [card(preview, q).getByRole("button", { name: "Use this design" }), preview.getByText("Could the reply have more room?", { exact: true })],
    hidden: [card(preview, q).getByText("Decision recorded", { exact: true })],
  });
  await card(preview, q).getByRole("button", { name: "Use this design" }).click();
  await expect(card(preview, q).getByText("Decision recorded", { exact: true })).toBeVisible();
  expect((await task(request)).hold_merge).toBe(initial.hold_merge);
  await previewWalk.state("05-design-accepted-hold-retained", {
    visible: [card(preview, q).getByText("Decision recorded", { exact: true }), card(preview, q).getByRole("link", { name: "View proposal · v1", exact: true })],
    hidden: [card(preview, q).getByRole("button", { name: "Use this design" })],
  });
  await preview.close();
});

test("a replacement labels the earlier saved proposal and returns to its exact question revision", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const q = (await task(request)).question;
  await walk.open(q.design_url);
  await expect(page.getByRole("img", { name: "Phone conversation", exact: true })).toBeVisible();
  expect((await request.post("/fixture/revise")).ok()).toBe(true);
  const current = (await task(request)).question;
  expect(current.id).toBe(q.id);
  expect(current.revision).toBe(q.revision + 1);
  await walk.state("earlier-fixed-version", {
    visible: [page.getByText("Earlier version.", { exact: false }), page.getByRole("link", { name: "Open current question", exact: true })],
    hidden: [page.getByText("The revised proposal", { exact: false })],
  });
  await expect(page.getByRole("link", { name: "← Back to question", exact: true })).toHaveAttribute("href", atQuestion(q));
  await page.getByRole("link", { name: "← Back to question", exact: true }).click();
  await expect(card(page, q)).toBeVisible();
  await expect(card(page, q).getByRole("button", { name: "Use this design" })).toHaveCount(0);
  await walk.open(current.design_url);
  await walk.state("replacement-version", {
    visible: [page.getByText("Proposal · v2", { exact: true }), page.getByText("The revised proposal", { exact: false })],
    hidden: [page.getByText("Earlier version.", { exact: false })],
  });
  expect((await task(request)).question.status).toBe("open");
});

test("proposal and screenshot loading, unavailable, denied and failed reads recover explicitly", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const q = (await task(request)).question;
  const endpoint = `**/api/design/atlas/${slug}/${q.id}/${q.revision}`;
  const heading = page.getByRole("main").getByRole("heading", { name: "Conversation layout", exact: true });
  const unavailable = page.getByRole("heading", { name: "Design unavailable", exact: true });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(endpoint, async (route) => { await gate; await route.continue(); }, { times: 1 });
  await walk.open(q.design_url);
  try {
    await walk.state("loading-proposal", { visible: [page.getByText("Loading proposal…", { exact: true })], hidden: [heading] });
  } finally { release(); }
  await expect(heading).toBeVisible();
  const images = "**/design/atlas/tasks/**";
  await page.route(images, (route) => route.fulfill({ status: 404, body: "Unavailable" }));
  await page.reload();
  const firstImage = page.getByRole("figure", { name: "Phone conversation", exact: true });
  await walk.state("screenshot-unavailable", {
    visible: [firstImage.getByText("Screenshot unavailable.", { exact: false }), firstImage.getByRole("button", { name: "Retry screenshot" })],
    hidden: [firstImage.getByRole("img"), firstImage.getByRole("link", { name: "Full size ↗", exact: true })],
  });
  await page.unroute(images);
  let releaseImage!: () => void;
  const imageGate = new Promise<void>((resolve) => { releaseImage = resolve; });
  await page.route(images, async (route) => { await imageGate; await route.continue(); }, { times: 1 });
  await firstImage.getByRole("button", { name: "Retry screenshot" }).click();
  try {
    await walk.state("loading-screenshot", { visible: [firstImage.getByText("Loading screenshot…", { exact: true })], hidden: [firstImage.getByRole("button", { name: "Retry screenshot" })] });
  } finally { releaseImage(); }
  await walk.state("screenshot-recovered", { visible: [firstImage.getByRole("img")], hidden: [firstImage.getByRole("button", { name: "Retry screenshot" }), firstImage.getByText("Loading screenshot…", { exact: true })] });
  for (const status of [403, 503, 409]) {
    await page.route(endpoint, (route) => route.fulfill({ status, json: { error: "Fixture read failure" } }));
    await page.reload();
    await walk.state(`proposal-read-${status}`, {
      visible: [unavailable, page.getByRole("button", { name: "Retry", exact: true }), page.getByRole("link", { name: "← Back to question", exact: true })],
      hidden: [heading, page.getByRole("img")],
    });
    await page.unroute(endpoint);
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(heading).toBeVisible();
  }
  await walk.state("proposal-recovered", { visible: [heading, firstImage.getByRole("img")], hidden: [unavailable, page.getByRole("button", { name: "Retry", exact: true })] });
  expect((await request.post("/fixture/damage-snapshot")).ok()).toBe(true);
  await walk.state("changed-snapshot-unavailable", { visible: [unavailable, page.getByRole("button", { name: "Retry", exact: true })], hidden: [heading, page.getByRole("img")] });
  await walk.open(q.design_url.replace(/\/1$/, "/99"));
  await walk.state("missing-proposal", { visible: [unavailable, page.getByRole("link", { name: "← Back to question", exact: true })], hidden: [heading, page.getByRole("img")] });
  expect((await task(request)).question.status).toBe("open");
});
