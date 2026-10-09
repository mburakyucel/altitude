import { expect, type Locator, type Page, type Route } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "l2-progress-service.py" });

/** Chromium's touch input drives real event dispatch, hit testing and native scrolling. */
async function finger(page: Page, x: number, y: number) {
  const input = await page.context().newCDPSession(page);
  await input.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
  let at = { x, y };
  return {
    /** Moves in steps; a pause after the last step counts as rest, so the release speed is zero. */
    async move(dx: number, dy = 0, pause = 0) {
      const from = at;
      for (let step = 1; step <= 8; step++) {
        at = { x: from.x + dx * step / 8, y: from.y + dy * step / 8 };
        await input.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [at] });
      }
      if (pause) await page.waitForTimeout(pause);
    },
    async lift() {
      try { await input.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] }); } finally { await input.detach(); }
    },
  };
}

/** A whole swipe: distance decides the outcome (half the width completes), never the runner's speed. */
async function swipe(page: Page, x: number, y: number, dx: number, dy = 0) {
  const touch = await finger(page, x, y);
  await touch.move(dx, dy, 150);
  await touch.lift();
}

const offset = (track: Locator) => track.evaluate((node) => parseFloat(node.style.transform.replace(/[^\d.-]/g, "")) || 0);

test("@phone-only content swipes preserve both reading positions, draft and selection without adding history", { tag: "@chromium" }, async ({ page, request }, info) => {
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
  await swipe(page, 290, 320, -220, 18);
  await walk.state("02-left-swipe-live", { visible: [live, page.getByRole("button", { name: "Stop", exact: true })], hidden: [conversation] });
  await expect(page).toHaveURL(`${path}/live`);
  await live.getByRole("button", { name: "Pause", exact: true }).click();
  const liveScroll = live.locator(".live-body");
  await expect(live.getByText("Session checkpoint 40: the original page stays available.")).toBeVisible();
  const tool = live.locator("details.session-tool").filter({ hasText: "pnpm test --run" });
  await tool.locator("summary").click();
  await expect(tool).toHaveAttribute("open", "");
  await liveScroll.evaluate((node) => { node.scrollTop = 180; });
  await swipe(page, 100, 340, 220);
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
  // Terminal is the last view: past it there is no wrap.
  await tabs.getByRole("link", { name: "Terminal", exact: true }).click();
  await expect(page.getByText("Terminal is off", { exact: true })).toBeVisible();
  await swipe(page, 290, 400, -220);
  await expect(page).toHaveURL(`${path}/terminal`);
  await tabs.getByRole("link", { name: "Conversation", exact: true }).click();
  await expect(conversation).toBeVisible();
  await swipe(page, 100, 320, 220);
  await expect(conversation).toBeVisible(); // There is no wrap before the first view.
  expect(await page.evaluate(() => history.length)).toBe(entries);
  await walk.state("04-accessible-tabs-and-reading-retained", { visible: [field, tabs], hidden: [live] });
});

test("@phone-only vertical scrolling, selected text, composer, screen edges and wide session content own their gestures", { tag: "@chromium" }, async ({ page, request }, info) => {
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
  // A long command truncates in its row and never widens the Live session past the screen; the
  // fixture's command is short, so this names a longer one on the rendered row.
  await control("long-call");
  const call = live.locator(".session-call").last();
  await expect(call).toBeVisible();
  await call.evaluate((node) => { node.textContent = `rg -n "cursor|generation" ${"src/search/pagination/".repeat(4)}`; });
  const viewport = page.viewportSize()!.width;
  const edge = await live.getByRole("button", { name: "Raw events", exact: true }).boundingBox();
  expect(edge!.x + edge!.width).toBeLessThanOrEqual(viewport);
  expect(await call.evaluate((node) => node.scrollWidth - node.clientWidth)).toBeGreaterThan(0);
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

test("@phone-only the swipe tracks the finger: reveal, load, settle, spring back, end resistance and reduced motion", { tag: "@chromium" }, async ({ page, request }, info) => {
  const slug = (await (await request.get("/fixture/status")).json()).tasks[0].slug;
  const control = async (mode: string, text?: string) => expect((await request.post("/fixture/control", { data: { slug, mode, text } })).ok()).toBe(true);
  await control("history");
  await control("output", Array.from({ length: 30 }, (_, n) => `Session checkpoint ${n + 1}: the view slides in with its content.`).join("\n\n"));
  const walk = walkthrough(page, info);
  const path = `/projects/atlas/tasks/${slug}`;
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const live = page.getByRole("region", { name: "Live session", exact: true });
  const track = page.locator(".task-track");
  const tabs = page.getByRole("navigation", { name: "Task views" });
  const latest = live.getByText("Session checkpoint 30: the view slides in with its content.");
  // The first reveal finds the transcript still loading: the drag shows Live session's skeleton.
  const held: Route[] = [];
  let holding = true;
  await page.route("**/api/transcript/**", (route) => holding ? held.push(route) : route.continue());
  await walk.open(path);
  await walk.state("01-idle-conversation", { visible: [conversation, tabs], hidden: [live] });
  let touch = await finger(page, 300, 400);
  await touch.move(-40);
  await expect.poll(() => offset(track)).toBe(-40);
  await walk.state("02-drag-started", { visible: [conversation, live], hidden: [] });
  await touch.move(-155, 0, 200);
  await expect.poll(() => offset(track)).toBe(-195);
  expect(held.length).toBeGreaterThan(0);
  await walk.state("03-half-way-incoming-loading", { visible: [conversation, live, live.getByLabel("Connecting")], hidden: [latest] });
  await touch.lift();
  await expect(page).toHaveURL(`${path}/live`);
  await expect.poll(() => offset(track)).toBe(0);
  await walk.state("04-release-completes-still-loading", { visible: [live, live.getByLabel("Connecting")], hidden: [conversation] });
  holding = false;
  for (const route of held) await route.continue();
  await walk.state("05-content-arrives", { visible: [live, latest], hidden: [conversation, live.getByLabel("Connecting")] });
  await swipe(page, 90, 400, 220);
  await expect(page).toHaveURL(path);
  await expect(conversation).toBeVisible();
  // Once rendered, the incoming view shows its real content at any offset; a short release springs back.
  touch = await finger(page, 300, 400);
  await touch.move(-195, 0, 100);
  await walk.state("06-half-way-incoming-rendered", { visible: [conversation, live, latest], hidden: [] });
  await touch.move(115, 0, 200);
  await expect.poll(() => offset(track)).toBe(-80);
  await touch.lift();
  await expect(live).toBeHidden();
  await expect(page).toHaveURL(path);
  await walk.state("07-release-springs-back", { visible: [conversation], hidden: [live] });
  // Past either end the track gives a little and never switches.
  touch = await finger(page, 90, 400);
  await touch.move(120, 0, 100);
  // The track applies a move on its next frame; wait for it before measuring the resistance.
  await expect.poll(() => offset(track)).toBeGreaterThan(0);
  expect(await offset(track)).toBeLessThan(60);
  await walk.state("08-end-resistance-before-conversation", { visible: [conversation], hidden: [] });
  await touch.lift();
  await expect.poll(() => offset(track)).toBe(0);
  await expect(page).toHaveURL(path);
  // The fixture task has a worktree, so Terminal is the last view.
  await tabs.getByRole("link", { name: "Terminal", exact: true }).click();
  const terminalOff = page.getByText("Terminal is off", { exact: true });
  await expect(terminalOff).toBeVisible();
  touch = await finger(page, 300, 400);
  await touch.move(-120, 0, 100);
  await expect.poll(() => offset(track)).toBeLessThan(0);
  expect(await offset(track)).toBeGreaterThan(-60);
  await walk.state("09-end-resistance-after-terminal", { visible: [terminalOff], hidden: [live] });
  await touch.lift();
  await expect.poll(() => offset(track)).toBe(0);
  await expect(page).toHaveURL(`${path}/terminal`);
  await tabs.getByRole("link", { name: "Conversation", exact: true }).click();
  await expect(conversation).toBeVisible();
  // Reduced motion: nothing moves with the finger; a release past the threshold switches at once.
  await page.emulateMedia({ reducedMotion: "reduce" });
  touch = await finger(page, 300, 400);
  await touch.move(-200, 0, 100);
  expect(await offset(track)).toBe(0);
  await walk.state("10-reduced-motion-drag-holds-still", { visible: [conversation], hidden: [live] });
  await touch.lift();
  await expect(page).toHaveURL(`${path}/live`);
  await walk.state("11-reduced-motion-instant-switch", { visible: [live, latest], hidden: [conversation] });
});
