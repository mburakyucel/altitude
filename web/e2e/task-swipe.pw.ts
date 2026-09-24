import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "l2-progress-service.py" });

/** Chromium's touch input drives real event dispatch, hit testing and native scrolling. */
async function swipe(page: Page, x: number, y: number, dx: number, dy = 0) {
  const input = await page.context().newCDPSession(page);
  try {
    await input.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
    for (let step = 1; step <= 8; step++) {
      await input.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x + dx * step / 8, y: y + dy * step / 8 }] });
    }
    await input.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  } finally { await input.detach(); }
}

test("@phone-only content swipes preserve both reading positions, draft and selection without adding history", async ({ page, request }, info) => {
  const slug = (await (await request.get("/fixture/status")).json()).tasks[0].slug;
  const control = async (mode: string, text?: string) => expect((await request.post("/fixture/control", { data: { slug, mode, text } })).ok()).toBe(true);
  await control("history");
  await control("output", Array.from({ length: 40 }, (_, n) => `Session checkpoint ${n + 1}: the original page stays available.`).join("\n\n"));
  await control("long-call");
  const walk = walkthrough(page, info);
  const path = `/projects/atlas/tasks/${slug}`;
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const live = page.getByRole("region", { name: "Live session", exact: true });
  const field = conversation.getByRole("textbox", { name: "Message the L2", exact: true });
  const scroller = conversation.locator(".convo-scroll");
  const tabs = page.getByRole("navigation", { name: "Task views" });
  await walk.open(path);
  await field.fill("Keep this unsent correction.");
  await field.evaluate((node: HTMLTextAreaElement) => {
    node.setSelectionRange(5, 9);
    node.dispatchEvent(new Event("select", { bubbles: true }));
    node.blur();
  });
  await expect(async () => {
    await scroller.evaluate((node) => { node.scrollTop = 200; });
    await expect(conversation.getByRole("button", { name: "Latest messages", exact: true })).toBeVisible({ timeout: 250 });
    expect(await scroller.evaluate((node) => node.scrollTop)).toBe(200);
  }).toPass();
  const entries = await page.evaluate(() => history.length);
  await walk.state("01-older-conversation", { visible: [field, tabs], hidden: [live] });
  await swipe(page, 290, 320, -100, 18);
  await walk.state("02-left-swipe-live", { visible: [live, page.getByRole("button", { name: "Stop", exact: true })], hidden: [conversation] });
  await expect(page).toHaveURL(`${path}/live`);
  await live.getByRole("button", { name: "Pause", exact: true }).click();
  const liveScroll = live.locator(".live-body");
  await expect(live.getByText("Session checkpoint 40: the original page stays available.")).toBeVisible();
  const tool = live.locator("details.session-tool").filter({ hasText: "pnpm test --run" });
  await tool.locator("summary").click();
  await expect(tool).toHaveAttribute("open", "");
  await liveScroll.evaluate((node) => { node.scrollTop = 180; });
  await swipe(page, 100, 340, 180);
  await walk.state("03-right-swipe-conversation-restored", { visible: [field], hidden: [live] });
  await expect(page).toHaveURL(path);
  await expect(field).toHaveValue("Keep this unsent correction.");
  await expect(field).not.toBeFocused();
  expect(await field.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([5, 9]);
  expect(await scroller.evaluate((node) => node.scrollTop)).toBeCloseTo(200, 0);
  await tabs.getByRole("link", { name: "Live session", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(live.getByRole("button", { name: "Follow", exact: true })).toBeVisible();
  await expect(tool).toHaveAttribute("open", "");
  expect(await liveScroll.evaluate((node) => node.scrollTop)).toBeCloseTo(180, 0);
  await swipe(page, 290, 340, -180);
  await expect(live).toBeVisible(); // There is no wrap beyond the last view.
  await tabs.getByRole("link", { name: "Conversation", exact: true }).click();
  await expect(conversation).toBeVisible();
  await swipe(page, 100, 320, 180);
  await expect(conversation).toBeVisible(); // There is no wrap before the first view.
  expect(await page.evaluate(() => history.length)).toBe(entries);
  await walk.state("04-accessible-tabs-and-reading-retained", { visible: [field, tabs], hidden: [live] });
});

test("@phone-only vertical scrolling, selected text, composer, screen edges and wide session content own their gestures", async ({ page, request }, info) => {
  const slug = (await (await request.get("/fixture/status")).json()).tasks[0].slug;
  const control = async (mode: string, text?: string) => expect((await request.post("/fixture/control", { data: { slug, mode, text } })).ok()).toBe(true);
  await control("history");
  await control("output", `\`\`\`text\n${"pagination-token-".repeat(50)}\n\`\`\``);
  const walk = walkthrough(page, info);
  await walk.open(`/projects/atlas/tasks/${slug}`);
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const scroller = conversation.locator(".convo-scroll");
  const live = page.getByRole("region", { name: "Live session", exact: true });
  const field = conversation.getByRole("textbox", { name: "Message the L2", exact: true });
  await expect(field).toBeVisible();
  await scroller.evaluate((node) => { node.scrollTop = 200; });
  await swipe(page, 200, 420, -30, -150);
  await expect(conversation).toBeVisible();
  await expect.poll(() => scroller.evaluate((node) => node.scrollTop)).toBeGreaterThan(200);
  const bubble = conversation.getByText("I will check the saved pagination behavior.", { exact: true });
  await bubble.evaluate((node) => { const range = document.createRange(); range.selectNodeContents(node); window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range); });
  await swipe(page, 290, 320, -180);
  await expect(conversation).toBeVisible();
  await page.evaluate(() => window.getSelection()!.removeAllRanges());
  await swipe(page, 385, 320, -180);
  await expect(conversation).toBeVisible();
  await field.fill("The composer keeps its own gestures.");
  const box = (await field.boundingBox())!;
  await swipe(page, box.x + box.width - 20, box.y + box.height / 2, -Math.min(140, box.width - 40));
  await expect(conversation).toBeVisible();
  await expect(field).toHaveValue("The composer keeps its own gestures.");
  await walk.state("01-conversation-gesture-exclusions", { visible: [field], hidden: [live] });
  await page.getByRole("link", { name: "Live session", exact: true }).click();
  const code = live.locator("pre").last();
  await expect(code).toBeVisible();
  await code.scrollIntoViewIfNeeded();
  const wrappedBox = (await code.boundingBox())!;
  await swipe(page, 100, wrappedBox.y + 30, 160);
  await expect(live).toBeVisible();
  // The app wraps prose code today. This named presentation variant exercises the browser's
  // horizontal overflow behavior as well as the ordinary pre gesture exclusion.
  await code.evaluate((node) => {
    node.style.width = `${node.clientWidth}px`;
    node.style.maxWidth = node.style.width;
    node.style.whiteSpace = "pre";
    node.style.overflowWrap = "normal";
  });
  info.annotations.push({ type: "horizontal overflow evidence", description: "Native touch scrolling uses a named nowrap presentation variant of the real session code; production code wraps its lines." });
  expect(await code.evaluate((node) => node.scrollWidth - node.clientWidth)).toBeGreaterThan(100);
  const codeBox = (await code.boundingBox())!;
  await code.evaluate((node) => { node.scrollLeft = 200; });
  await swipe(page, 100, codeBox.y + Math.min(20, codeBox.height / 2), 160);
  await expect(live).toBeVisible();
  await expect.poll(() => code.evaluate((node) => node.scrollLeft)).toBeLessThan(200);
  await walk.state("02-horizontal-code-scroll-keeps-live-view", { visible: [live, code], hidden: [conversation] });
});
