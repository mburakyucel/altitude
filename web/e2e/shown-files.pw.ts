import { expect, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "shown-files-service.py" });

type Fixture = { slug: string; readme: string; screens: string; hostile: string; hostile_path: string;
  question: { id: string; revision: number }; legacy: { slug: string; id: string } };
async function fixture(request: APIRequestContext) {
  const response = await request.get("/fixture/shown");
  expect(response.ok()).toBe(true);
  return response.json() as Promise<Fixture>;
}
async function record(request: APIRequestContext, slug: string) {
  const response = await request.get(`/api/task/atlas/${slug}`);
  expect(response.ok()).toBe(true);
  return response.json() as Promise<{ hold_merge: string; question: { id: string; revision: number; status: string } }>;
}
const post = async (request: APIRequestContext, path: string) => {
  const response = await request.post(path);
  expect(response.ok()).toBe(true);
  return response.json();
};
const taskPath = (slug: string) => `/projects/atlas/tasks/${slug}`;
const conversation = (page: Page) => page.getByRole("region", { name: "Task conversation", exact: true });
const draft = (page: Page) => page.getByRole("textbox", { name: "Message the L2", exact: true });
const phone = (info: TestInfo) => info.project.name === "phone";
/** Every request a page makes stays on Altitude's own origin. */
function sameOrigin(page: Page, service: string) {
  const outside: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol !== "data:" && url.origin !== new URL(service).origin) outside.push(request.url());
  });
  return outside;
}

test("a reply's document opens in the reader and closes back to the conversation with the draft intact", async ({ page, request, service }, info) => {
  const { slug, readme } = await fixture(request);
  const walk = walkthrough(page, info);
  const outside = sameOrigin(page, service);
  await walk.open(taskPath(slug));
  const card = conversation(page).getByRole("link", { name: "Open README.md", exact: true });
  await card.scrollIntoViewIfNeeded();
  await walk.state("01-page-card", {
    visible: [card, card.getByText("Lumen — Lumen turns noisy logs into a timeline you can read on any screen.", { exact: false }), card.locator(".shown-cover img")],
    hidden: [card.getByText("Earlier", { exact: true })],
  });
  await expect(card).toHaveAttribute("href", `${taskPath(slug)}/view/${readme}`);
  if (phone(info)) expect((await card.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await draft(page).fill("Could the install step come first?");
  await card.click();
  await expect(page).toHaveURL(`${taskPath(slug)}/view/${readme}`);
  const reader = page.getByRole("region", { name: "README.md", exact: true });
  const close = reader.getByRole("button", { name: "Close", exact: true });
  await walk.state("02-reader", {
    visible: [reader.getByRole("heading", { name: "Lumen", level: 1 }), reader.getByRole("button", { name: "Open image overview.png", exact: true }),
      reader.getByRole("button", { name: "Open image phone.png", exact: true }), reader.getByRole("table"), close],
    hidden: [reader.getByRole("status")],
  });
  // A remote image stays a link to its address; an image that was not copied names its path.
  await expect(reader.getByRole("link", { name: "Build", exact: true })).toHaveAttribute("href", "https://example.org/badge.svg");
  await expect(reader.locator(".md-not-shown")).toHaveText("Image not showndocs/images/missing.png");
  await expect(reader.getByRole("link", { name: "project site", exact: true })).toHaveAttribute("target", "_blank");
  await expect(reader.getByText("guide", { exact: true })).toHaveAttribute("title", "docs/guide.md");
  if (phone(info)) {
    await expect(page.locator(".task-page")).toHaveAttribute("inert", "");
  } else {
    await expect(reader).toHaveAttribute("data-panel");
    await expect(conversation(page)).toBeVisible();
    await expect(card).toHaveAttribute("aria-current", "page");
  }
  // A heading link scrolls inside the document.
  await reader.getByRole("link", { name: "Install", exact: true }).click();
  await expect(reader.getByRole("heading", { name: "Install", level: 2 })).toBeInViewport();
  await expect(page).toHaveURL(`${taskPath(slug)}/view/${readme}`);

  await close.click();
  await expect(page).toHaveURL(taskPath(slug));
  await walk.state("03-closed-draft-intact", { visible: [card, draft(page)], hidden: [reader] });
  await expect(draft(page)).toHaveValue("Could the install step come first?");
  // Browser Forward reopens it and Back closes it again; Escape closes it too.
  await page.goForward();
  await expect(reader).toBeVisible();
  await page.goBack();
  await expect(reader).toBeHidden();
  await card.click();
  await reader.getByRole("heading", { name: "Lumen", level: 1 }).click();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(taskPath(slug));
  await expect(draft(page)).toHaveValue("Could the install step come first?");
  if (!phone(info)) {
    // Below the side-panel width the reader covers the screen, as on a phone.
    await page.setViewportSize({ width: 1100, height: 800 });
    await walk.open(taskPath(slug));
    await draft(page).fill("Could the install step come first?");
    await card.click();
    await walk.state("04-narrow-desktop-reader", { visible: [reader.getByRole("heading", { name: "Lumen", level: 1 })], hidden: [] });
    await expect(reader).not.toHaveAttribute("data-panel");
    await expect(page.locator(".task-page")).toHaveAttribute("inert", "");
    await close.click();
    await expect(draft(page)).toHaveValue("Could the install step come first?");
  }
  expect(outside).toEqual([]);
});

test("the gallery moves through a set's images and Back closes only the gallery", async ({ page, request }, info) => {
  const { slug, readme } = await fixture(request);
  const walk = walkthrough(page, info);
  await walk.open(taskPath(slug));
  const thumbs = conversation(page).getByLabel("Shown images", { exact: true });
  await thumbs.scrollIntoViewIfNeeded();
  await walk.state("01-thumbnails", {
    visible: [thumbs.getByRole("button", { name: "Open image timeline.png", exact: true }), thumbs.getByRole("button", { name: "Open 2 more images", exact: true })],
    hidden: [thumbs.getByRole("button", { name: "Open image export.png", exact: true })],
  });
  await thumbs.getByRole("button", { name: "Open 2 more images", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await walk.state("02-gallery-fourth-of-five", {
    visible: [dialog.getByText("export.png", { exact: true }), dialog.getByText("4 / 5", { exact: true }), dialog.getByRole("img"),
      dialog.getByRole("button", { name: "Previous image" }), dialog.getByRole("button", { name: "Next image" })],
    hidden: [dialog.getByText("Loading image…", { exact: true })],
  });
  await dialog.getByRole("button", { name: "Next image" }).click();
  await walk.state("03-gallery-last", { visible: [dialog.getByText("5 / 5", { exact: true })], hidden: [dialog.getByRole("button", { name: "Next image" })] });
  await page.keyboard.press("ArrowLeft");
  await expect(dialog.getByText("4 / 5", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Zoom image", exact: true }).click();
  await walk.state("04-gallery-zoomed", { visible: [dialog.getByRole("button", { name: "Fit image", exact: true })], hidden: [] });
  await page.goBack();
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(taskPath(slug));

  // From the reader: Escape closes the gallery, then the reader; Forward never revives a closed gallery.
  await walk.open(`${taskPath(slug)}/view/${readme}`);
  const reader = page.getByRole("region", { name: "README.md", exact: true });
  await reader.getByRole("button", { name: "Open image phone.png", exact: true }).click();
  await expect(dialog.getByText("2 / 3", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(reader).toBeVisible();
  await page.goForward();
  await expect(dialog).toBeHidden();
  await expect(reader).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(reader).toBeHidden();
});

test("a later version marks the earlier card, and Newer opens the latest", async ({ page, request }, info) => {
  const { slug, readme } = await fixture(request);
  const { newer } = await post(request, "/fixture/republish");
  const walk = walkthrough(page, info);
  await walk.open(taskPath(slug));
  const cards = conversation(page).getByRole("link", { name: "Open README.md", exact: true });
  await expect(cards).toHaveCount(2);
  await cards.first().scrollIntoViewIfNeeded();
  await walk.state("01-earlier-and-current-cards", { visible: [cards.first().getByText("Earlier", { exact: true })], hidden: [cards.last().getByText("Earlier", { exact: true })] });
  await cards.first().click();
  const reader = page.getByRole("region", { name: "README.md", exact: true });
  const pill = reader.getByRole("button", { name: "Newer", exact: true });
  await walk.state("02-earlier-reader", { visible: [pill, reader.getByText("Lumen turns noisy logs", { exact: false })], hidden: [] });
  await pill.click();
  await expect(page).toHaveURL(`${taskPath(slug)}/view/${newer}`);
  await walk.state("03-newer-reader", { visible: [reader.getByText("Lumen turns long, noisy logs", { exact: false })], hidden: [pill] });
  // Newer replaced the earlier entry, so closing returns straight to the conversation.
  await reader.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page).toHaveURL(taskPath(slug));
  await page.goBack();
  await expect(page).not.toHaveURL(`${taskPath(slug)}/view/${readme}`);
});

test("a question's files open from Needs you and the task; viewing and a follow-up approve nothing, and approval keeps the hold", async ({ page, request }, info) => {
  const { slug, question } = await fixture(request);
  const initial = await record(request, slug);
  const walk = walkthrough(page, info);
  const view = `${taskPath(slug)}/view/${question.id}/${question.revision}`;
  await walk.open("/");
  const inbox = page.locator(`[data-question-id="${question.id}"]`);
  const card = inbox.getByRole("link", { name: "Open PROPOSAL.md", exact: true });
  await walk.state("01-needs-you-card", {
    visible: [card, card.getByText("Proposal: a shorter README — The README opens", { exact: false }), inbox.getByRole("button", { name: "Build as proposed" })],
    hidden: [],
  });
  await inbox.getByRole("button", { name: "Other…", exact: true }).click();
  await inbox.getByRole("textbox").fill("Keep the badges.");
  await card.click();
  await expect(page).toHaveURL(view);
  const reader = page.getByRole("region", { name: "PROPOSAL.md", exact: true });
  await walk.state("02-proposal-reader", {
    visible: [reader.getByRole("heading", { name: "Proposal: a shorter README", level: 1 }), reader.getByRole("button", { name: "Open image phone.png", exact: true })],
    hidden: [reader.getByRole("button", { name: "Build as proposed" })],
  });
  await reader.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page).toHaveURL("/");
  await expect(inbox).toBeInViewport();
  await expect(inbox.getByRole("textbox")).toHaveValue("Keep the badges.");
  // The legacy preview saved before shown files names itself and does not open.
  const legacy = page.getByText("Compact rows", { exact: true }).locator("xpath=ancestor::div[@data-gone]");
  await legacy.scrollIntoViewIfNeeded();
  await walk.state("03-earlier-preview-unavailable", { visible: [legacy.getByText("Preview unavailable", { exact: true })], hidden: [legacy.getByRole("link")] });
  expect(await record(request, slug)).toEqual(initial);

  // A follow-up from the task hands the turn back without answering.
  await walk.open(`${taskPath(slug)}?question=${question.id}&revision=${question.revision}`);
  await draft(page).fill("Could the phone picture be smaller?");
  await conversation(page).getByRole("button", { name: "Send", exact: true }).click();
  await expect(conversation(page).getByText("Sent · the L2 has your reply.", { exact: true })).toBeVisible();
  expect((await record(request, slug)).question.status).toBe("open");
  await post(request, "/fixture/park");
  await page.reload();
  const asked = page.locator(`[data-question-id="${question.id}"][data-question-revision="${question.revision}"]`);
  await asked.getByRole("button", { name: "Build as proposed" }).click();
  await page.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect(asked).toHaveCount(0);
  await post(request, "/fixture/checkpoint");
  await page.reload();
  const decided = page.locator(`[data-question-id="${question.id}"][data-question-revision="${question.revision}"]`);
  await walk.state("04-decided-files-still-open", {
    visible: [decided.getByText("Decision recorded", { exact: true }), decided.getByRole("link", { name: "Open PROPOSAL.md", exact: true })],
    hidden: [decided.getByRole("button", { name: "Build as proposed" })],
  });
  const after = await record(request, slug);
  expect(after.question.status).toBe("resolved");
  expect(after.hold_merge).toBe(initial.hold_merge);
});

test("a revised question binds the answer to its new files and the earlier revision points to them", async ({ page, request }, info) => {
  const { slug, question } = await fixture(request);
  const revised = await post(request, "/fixture/revise");
  expect(revised).toEqual({ id: question.id, revision: question.revision + 1 });
  const walk = walkthrough(page, info);
  await walk.open(`${taskPath(slug)}/view/${question.id}/${question.revision}`);
  const reader = page.getByRole("region", { name: "PROPOSAL.md", exact: true });
  await walk.state("01-earlier-revision", { visible: [reader.getByRole("button", { name: "Newer", exact: true }), reader.getByText("one picture of each screen", { exact: false })], hidden: [] });
  await reader.getByRole("button", { name: "Newer", exact: true }).click();
  await expect(page).toHaveURL(`${taskPath(slug)}/view/${question.id}/${revised.revision}`);
  await expect(reader.getByText("a picture of each screen", { exact: false })).toBeVisible();
  await reader.getByRole("button", { name: "Close", exact: true }).click();
  const current = page.locator(`[data-question-id="${question.id}"][data-question-revision="${revised.revision}"]`);
  await expect(current.getByRole("link", { name: "Open PROPOSAL.md", exact: true })).toHaveAttribute("href", `${taskPath(slug)}/view/${question.id}/${revised.revision}`);
  await expect(current.getByRole("button", { name: "Build as proposed" })).toBeVisible();
  expect((await record(request, slug)).question).toMatchObject({ revision: revised.revision, status: "open" });
});

test("loading, denied, failed and changed files explain themselves and recover", async ({ page, request }, info) => {
  const { slug, readme } = await fixture(request);
  const walk = walkthrough(page, info);
  const view = `${taskPath(slug)}/view/${readme}`;
  const api = `**/api/shown/atlas/${slug}/${readme}`;
  const reader = page.getByRole("region", { name: "README.md", exact: true });
  const heading = reader.getByRole("heading", { name: "Lumen", level: 1 });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(api, async (route) => { await gate; await route.continue(); }, { times: 1 });
  await walk.open(view);
  try {
    await walk.state("01-reader-loading", { visible: [reader.getByRole("status", { name: "Loading README.md" })], hidden: [heading] });
  } finally { release(); }
  await expect(heading).toBeVisible();
  for (const [status, text, icon] of [[403, "This device has no access right now.", "denied"], [503, "It could not be loaded. Try again, or ask in the conversation.", "failed"]] as const) {
    await page.route(api, (route) => route.fulfill({ status, json: { error: "Fixture failure" } }));
    await page.reload();
    await walk.state(`02-reader-${icon}`, {
      visible: [reader.getByRole("heading", { name: "Preview unavailable", exact: true }), reader.getByText(text, { exact: true }),
        reader.getByRole("button", { name: "Retry", exact: true }), reader.getByRole("button", { name: "Close", exact: true })],
      hidden: [heading],
    });
    await page.unroute(api);
    await reader.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(heading).toBeVisible();
  }

  // Saved images that fail: the reader's own Retry, a thumbnail that becomes its Retry, a card without its cover.
  const files = "**/api/shown/**/files/**";
  await page.route(files, (route) => route.fulfill({ status: 404, body: "Unavailable" }));
  await page.reload();
  const extra = reader.getByRole("group", { name: "overview.png unavailable", exact: true });
  await walk.state("03-reader-image-failed", {
    visible: [heading, extra.getByText("Image unavailable", { exact: true }), extra.getByRole("button", { name: "Retry", exact: true })],
    hidden: [reader.getByRole("button", { name: "Open image overview.png", exact: true })],
  });
  await page.unroute(files);
  await extra.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(reader.getByRole("button", { name: "Open image overview.png", exact: true })).toBeVisible();
  await page.route(files, (route) => route.fulfill({ status: 404, body: "Unavailable" }));
  await walk.open(taskPath(slug));
  const card = conversation(page).getByRole("link", { name: "Open README.md", exact: true });
  const thumbs = conversation(page).getByLabel("Shown images", { exact: true });
  const retryThumb = thumbs.getByRole("button", { name: "Retry image timeline.png", exact: true });
  await thumbs.scrollIntoViewIfNeeded();
  await walk.state("04-cover-and-thumbnails-failed", { visible: [card, retryThumb], hidden: [card.locator(".shown-cover"), thumbs.locator("img")] });
  await page.unroute(files);
  await retryThumb.click();
  await expect(thumbs.getByRole("button", { name: "Open image timeline.png", exact: true })).toBeVisible();
  // A gallery image that fails retries in place; Zoom waits until it shows.
  await thumbs.getByRole("button", { name: "Open image timeline.png", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("img")).toBeVisible();
  await page.route(files, (route) => route.fulfill({ status: 404, body: "Unavailable" }));
  await dialog.getByRole("button", { name: "Next image" }).click();
  await walk.state("05-gallery-image-failed", {
    visible: [dialog.getByText("Image unavailable", { exact: true }), dialog.getByRole("button", { name: "Retry", exact: true }), dialog.getByText("2 / 5", { exact: true })],
    hidden: [dialog.getByRole("button", { name: "Zoom image" }), dialog.getByRole("img")],
  });
  await page.unroute(files);
  let releaseImage!: () => void;
  const imageGate = new Promise<void>((resolve) => { releaseImage = resolve; });
  await page.route((url) => url.searchParams.get("retry") === "1", async (route) => { await imageGate; await route.continue(); }, { times: 1 });
  await dialog.getByRole("button", { name: "Retry", exact: true }).click();
  try {
    await walk.state("06-gallery-image-loading", { visible: [dialog.getByRole("status")], hidden: [dialog.getByText("Image unavailable", { exact: true })] });
  } finally { releaseImage(); }
  await expect(dialog.getByRole("img", { name: "search.png" })).toBeVisible();
  await dialog.getByRole("button", { name: "Close image", exact: true }).click();
  await expect(dialog).toBeHidden();

  // A saved copy that no longer matches its record is a card that does not open.
  await post(request, "/fixture/damage");
  await page.reload();
  const gone = conversation(page).locator("[data-gone]").filter({ hasText: "README.md" });
  await gone.scrollIntoViewIfNeeded();
  await walk.state("07-changed-copy-unavailable", { visible: [gone.getByText("Preview unavailable", { exact: true })], hidden: [card] });
  await walk.open(view);
  await expect(reader.getByRole("heading", { name: "Preview unavailable", exact: true })).toBeVisible();
});

test("nothing in a shown or opened document runs or leaves Altitude's origin", async ({ page, request, service }, info) => {
  const { slug, hostile, hostile_path } = await fixture(request);
  const walk = walkthrough(page, info);
  const outside = sameOrigin(page, service);
  await walk.open(`${taskPath(slug)}/view/${hostile}`);
  const reader = page.getByRole("region", { name: "hostile.md", exact: true });
  await walk.state("01-hostile-reader", {
    visible: [reader.getByRole("heading", { name: "Hostile fixture", level: 1 }), reader.getByText("Styled paragraph", { exact: true }),
      reader.getByRole("link", { name: "Tracker", exact: true })],
    hidden: [reader.locator("script, iframe, form, input, picture, [style], [onclick], [onerror], [onload]")],
  });
  await expect(reader.getByRole("link", { name: "Remote picture", exact: true })).toHaveAttribute("href", "https://example.org/p.png");
  await expect(reader.getByText("Run", { exact: true }).locator("xpath=ancestor::a")).toHaveCount(0);
  await expect(reader.locator("img")).toHaveCount(0);
  expect(await page.evaluate(() => "shownExecuted" in window)).toBe(false);
  await walk.open(`/projects/atlas/file?${new URLSearchParams({ path: hostile_path })}`);
  const file = page.getByRole("region", { name: "File contents", exact: true });
  await walk.state("02-hostile-file-reader", {
    visible: [file.getByRole("heading", { name: "Hostile fixture", level: 1 })],
    hidden: [file.locator("script, iframe, form, input, picture, img, [style], [onclick], [onerror], [onload]")],
  });
  expect(await page.evaluate(() => "shownExecuted" in window)).toBe(false);
  expect(outside).toEqual([]);
});
