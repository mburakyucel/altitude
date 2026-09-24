import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/*
 * The chat on a small screen: reading anchors through the keyboard, queue recovery inside the usable
 * viewport, and the sent message that appears at once and settles in place (SPEC.md §3.6 Sending).
 */

// Browser layout simulation, not a claim about native iOS/Android keyboard events.
async function visualViewport(page: Page, height: number, top = 0, scale = 1) {
  await page.evaluate(({ height, top, scale }) => {
    const viewport = window.visualViewport!;
    Object.defineProperties(viewport, {
      height: { configurable: true, value: height },
      offsetTop: { configurable: true, value: top },
      scale: { configurable: true, value: scale },
    });
    viewport.dispatchEvent(new Event("resize"));
  }, { height, top, scale });
}

test("compact chat keeps bottom and older reading anchors through keyboard, details and restoration", async ({ page, request }, info) => {
  const phone = info.project.name === "phone";
  const project = await fixtureProject(request);
  await page.route(`**/api/chat/${project.name}?*`, async (route) => {
    const response = await route.fetch();
    const view = await response.json();
    await route.fulfill({ response, json: { ...view, active: null, busy: false, engine: null, queued: [], history:
      Array.from({ length: 36 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", trigger: "chat", turn_id: `reading-${Math.floor(i / 2)}`, text: `Message ${i}: Keep the index migration readable while the compatibility work proceeds.`, at: "2026-09-08T12:00:00Z" })) } });
  });
  const walk = walkthrough(page, info);
  await walk.open(project.path);
  const field = page.getByRole("textbox", { name: /^Message L3/ });
  const scroll = page.locator(".convo-scroll");
  const nav = page.getByRole("navigation", { name: phone ? "Primary" : "Rail" });
  const bottomGap = () => scroll.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);
  await expect(field).toBeVisible();
  await expect.poll(bottomGap).toBeLessThanOrEqual(1);
  await walk.state("01-reading", { visible: [nav, field, page.getByRole("button", { name: "More actions" })], hidden: [] });
  const readingHeight = await scroll.evaluate((el) => el.clientHeight);
  const heights: Record<string, number> = { reading: readingHeight };
  if (phone) expect(readingHeight).toBeGreaterThanOrEqual(586);

  await field.fill("Retain this draft and selection.");
  await field.evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(7, 11));
  await expect(nav).toBeVisible(); // Hardware keyboard: focus is insufficient.
  await visualViewport(page, 772);
  await expect(nav).toBeVisible(); // Small toolbar movement.
  await visualViewport(page, 510, 0, 1.5);
  await expect(nav).toBeVisible(); // Pinch zoom is not a keyboard.
  await visualViewport(page, 510);
  if (phone) await expect.poll(() => page.locator(".phone-header").evaluate((el) => el.getBoundingClientRect().top)).toBeGreaterThanOrEqual(0);
  await walk.state("02-keyboard-simulated", { visible: [field], hidden: phone ? [nav] : [] });
  heights.keyboard = await scroll.evaluate((el) => el.clientHeight);
  if (phone) {
    expect(await scroll.evaluate((el) => el.clientHeight)).toBeGreaterThanOrEqual(336);
    await expect(page.locator(".shell")).toHaveAttribute("data-keyboard", "");
  } else await expect(nav).toBeVisible();
  await expect.poll(bottomGap).toBeLessThanOrEqual(1);
  await visualViewport(page, 510, 28);
  if (phone) await expect.poll(() => page.locator(".shell").evaluate((el) => el.getBoundingClientRect().top)).toBe(28);

  await visualViewport(page, phone ? 844 : 900);
  await walk.state("03-keyboard-dismissed-with-focus", { visible: [nav, field], hidden: [] });
  await expect(field).toBeFocused();
  await expect(field).toHaveValue("Retain this draft and selection.");
  expect(await field.evaluate((el: HTMLTextAreaElement) => [el.selectionStart, el.selectionEnd])).toEqual([7, 11]);
  await expect.poll(bottomGap).toBeLessThanOrEqual(1);

  await scroll.evaluate(async (el) => {
    el.scrollTop = 450;
    // Finish the native scroll event and paint before simulating a separate keyboard action.
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  const anchor = scroll.locator(".turn").nth(4);
  const offset = () => anchor.evaluate((el) => el.getBoundingClientRect().top - el.closest(".convo-scroll")!.getBoundingClientRect().top);
  const olderOffset = await offset();
  await visualViewport(page, 510);
  await expect.poll(offset).toBeCloseTo(olderOffset, 0);
  await walk.state("04-older-reading-keyboard", { visible: [field], hidden: phone ? [nav] : [] });
  if (phone) {
    const opener = page.getByRole("button", { name: "More actions" });
    const details = page.getByRole("dialog", { name: "Project details" });
    await walk.state("05-project-details-above-keyboard", { action: () => opener.click(), visible: [details, details.getByRole("combobox", { name: "L3 engine" })], hidden: [] });
    const bounds = await details.boundingBox();
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(511);
    await page.keyboard.press("Shift+Tab");
    expect(await details.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    await page.keyboard.press("Escape");
    await expect(opener).toBeFocused();
    await expect.poll(offset).toBeCloseTo(olderOffset, 0);
  }
  await visualViewport(page, phone ? 844 : 900);
  await walk.state("06-older-reading-restored", { visible: [nav, field], hidden: [page.getByRole("dialog")] });
  await expect.poll(offset).toBeCloseTo(olderOffset, 0);
  await expect(field).toHaveValue("Retain this draft and selection.");
  expect(await field.evaluate((el: HTMLTextAreaElement) => [el.selectionStart, el.selectionEnd])).toEqual([7, 11]);
  await info.attach("message-area-pixels", { body: JSON.stringify({ viewport: page.viewportSize(), simulatedKeyboardHeight: 334, ...heights }), contentType: "application/json" });
});

test("queued removal failure and Retry stay inside the usable viewport", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  let removed = false;
  let attempts = 0;
  await page.route(`**/api/chat/${project.name}?*`, async (route) => {
    const response = await route.fetch();
    const view = await response.json();
    await route.fulfill({ response, json: { ...view, busy: true, active: null, queued: removed ? [] : [
      { id: "waiting-note", text: "Keep the migration reversible.", at: "2026-09-08T12:00:00Z", trigger: "chat" },
    ] } });
  });
  await page.route("**/api/chat/remove", (route) => {
    attempts++;
    if (attempts === 1) return route.fulfill({ status: 500, json: { error: "Queue temporarily unavailable" } });
    removed = true;
    return route.fulfill({ json: { ok: true } });
  });
  const walk = walkthrough(page, info);
  await walk.open(project.path);
  const field = page.getByRole("textbox", { name: /^Message L3/ });
  await field.fill("Keep this draft.");
  await visualViewport(page, 510);
  if (info.project.name === "phone") await expect(page.locator(".phone-status")).toContainText("L3 · Busy");
  const queued = page.getByRole("list", { name: "Queued messages" });
  const toast = page.locator(".toast");
  await walk.state("01-queue-remove-failed-keyboard", { action: () => queued.getByRole("button", { name: "Remove" }).click(), visible: [queued, toast, toast.getByRole("button", { name: "Retry" })], hidden: [] });
  const box = await toast.boundingBox();
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThanOrEqual(info.project.name === "phone" ? 510 : 900);
  await walk.state("02-queue-remove-retried", { action: () => toast.getByRole("button", { name: "Retry" }).click(), visible: [field], hidden: [queued, toast] });
  await expect(field).toHaveValue("Keep this draft.");
  expect(attempts).toBe(2);
});

test("a task message appears at once as a pending bubble and settles in place when the server accepts it", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const text = "Keep the migration reversible while the index rebuilds.";
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  // The slow link: admission waits for the gate, then the real handler stores the row and answers.
  await page.route("**/api/l2/message", async (route) => {
    await gate;
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    await route.fulfill({ response });
  }, { times: 1 });
  const walk = walkthrough(page, info);
  await walk.open(`${project.path}/tasks/prepare-index-migration`);
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const bubble = conversation.locator(".bubble", { hasText: text });
  const row = conversation.locator(".msg-row").filter({ hasText: text });
  const cue = row.getByRole("status", { name: "Sending", exact: true });
  const remove = row.getByRole("button", { name: "Remove", exact: true });
  await field.fill(text);
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  try {
    await walk.state("01-sent-pending-bubble-with-cue", { visible: [bubble, cue, row.getByText("Sending…", { exact: true })], hidden: [remove] });
    await expect(row).toHaveAttribute("data-pending", "true");
    await expect(row).toHaveCSS("opacity", "0.6");
    await expect(field).toHaveValue("");
    await row.evaluate((node) => node.setAttribute("data-walk", "pending"));
  } finally { release(); }
  await walk.state("02-settled-in-place", {
    visible: [bubble, row.getByText("Queued · waiting for a checkpoint", { exact: true }), remove],
    hidden: [cue, row.getByText("Sending…", { exact: true })],
  });
  await expect(row).not.toHaveAttribute("data-pending");
  await expect(row).toHaveCSS("opacity", "1");
  // The stored row settled the same node: nothing remounted, nothing duplicated.
  await expect(row).toHaveAttribute("data-walk", "pending");
  await expect(bubble).toHaveCount(1);
});
