import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject, fixtureTask } from "./fixture-data";
import { walkthrough } from "./walkthrough";

const hold = "The operator must inspect the phone reading and composing layouts, the complete desktop walkthrough, and the final green pull request before merging. The owner must keep this merge restriction in place through all checks and discussion.";
const block = "Keep the decision open until its owner can verify the expected behavior against the complete task brief. This longer explanation remains available without taking a permanent paragraph above the conversation, including while the software keyboard is open.";
const cases = [
  { key: "running-held", state: "running", label: "Running", held: true },
  { key: "waiting-l3", state: "blocked", label: "Waits for L3", held: false, audience: "l3" },
  { key: "waiting-l3-held", state: "blocked", label: "Waits for L3", held: true, audience: "l3" },
  { key: "operator-held", state: "blocked", label: "Needs your answer", held: true, audience: "operator" },
  { key: "fault-held", state: "blocked", label: "Blocked by a fault", held: true, fault: "browser" },
  { key: "paused", state: "blocked", label: "Paused", held: false },
];

for (const scene of cases) test(`task details: ${scene.key}, full reasons and restored reading`, async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const task = await fixtureTask(request, project.name);
  const question = scene.audience ? {
    id: "compact-question", revision: 1, anchor_id: "compact-anchor", status: "open", audience: scene.audience,
    project: project.name, slug: task.slug, title: task.title, kind: "asks", asked_by: "l2",
    question: "Should the existing validation scope stay in place?", asked: new Date().toISOString(),
    recommendation: null, resolution: null,
  } : null;
  const reason = scene.fault ? `The verification browser could not start. ${block}` : block;
  const messages = Array.from({ length: 24 }, (_, index) => ({ id: `compact-${index}`, at: "2026-09-08T12:00:00Z", role: "l2", text: `Reading context ${index + 1}: preserve the existing supported workflow while the changes are checked.` }));
  if (question) messages.splice(8, 0, { id: question.anchor_id, at: "2026-09-08T12:00:00Z", role: "l2", text: question.question });
  await page.route((url) => url.pathname === `/api/task/${project.name}/${task.slug}`, (route) => route.fulfill({ json: {
    ...task, state: scene.state, hold_merge: scene.held ? hold : "", blocked_reason: scene.state === "blocked" ? reason : "",
    resume_after: null, waiting_on: scene.audience === "l3" ? "l3" : "", fault: scene.fault ?? null,
    question, questions: question ? [question] : [], question_group: null, messages,
  } }));
  if (question?.audience === "operator") await page.route("**/api/overview", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), queue: [question] } });
  });
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const scroller = conversation.locator(".convo-scroll");
  const opener = page.getByRole("button", { name: "Task details", exact: true });
  const dialog = page.getByRole("dialog", { name: "Task details", exact: true });
  await walk.open(`/projects/${project.name}/tasks/${task.slug}`);
  if (!phone) await page.getByRole("button", { name: "Live session", exact: true }).click();
  await field.fill("Keep this unsent draft and its cursor.");
  await field.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(5, 9));
  const latest = page.getByRole("button", { name: "Latest messages", exact: true });
  const readingTop = 170;
  // Draft resizing can still be following the bottom. Establish older reading before testing
  // restoration: accepting any positive scrollTop also accepts that unrelated bottom position.
  await expect(async () => {
    await scroller.evaluate((node, top) => { node.scrollTop = top; }, readingTop);
    await expect(latest).toBeVisible({ timeout: 250 });
    expect(await scroller.evaluate((node) => node.scrollTop)).toBe(readingTop);
  }).toPass({ timeout: 5_000 });
  const readingAnchor = conversation.getByText(messages[2].text, { exact: true });
  const anchorOffset = () => readingAnchor.evaluate((node) => node.getBoundingClientRect().top - node.closest(".convo-scroll")!.getBoundingClientRect().top);
  await expect(readingAnchor).toBeInViewport();
  const readingOffset = await anchorOffset();
  await walk.state("01-reading-collapsed", {
    visible: [opener, field, latest, readingAnchor, page.getByText(scene.label, { exact: true }).first(), ...(scene.fault ? [page.getByText("The verification browser could not start. L3 has been told.")] : [])],
    hidden: [dialog, page.getByText(hold, { exact: true }), page.getByText(reason, { exact: true }), ...(phone ? [page.getByRole("button", { name: "Reject", exact: true })] : [])],
  });
  for (const keyboard of phone ? [false, true] : [false]) {
    const prefix = keyboard ? "03-keyboard" : "02-reading";
    if (keyboard) {
      await field.focus();
      await field.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(5, 9));
      await page.evaluate(() => {
        Object.defineProperty(window.visualViewport!, "height", { configurable: true, value: 510 });
        window.visualViewport!.dispatchEvent(new Event("resize"));
      });
      await expect(page.getByRole("navigation", { name: "Primary" })).toBeHidden();
      if (question?.audience === "operator") await expect(page.getByRole("link", { name: "Needs you, 1 pending" })).toBeVisible();
      expect(await page.locator(".task-state-line").evaluate((node) => node.scrollWidth - node.clientWidth), "Compact state and Merge held stay readable").toBeLessThanOrEqual(1);
    }
    await walk.state(`${prefix}-details`, {
      action: () => opener.click(), visible: [dialog, dialog.getByText(String(task.title), { exact: true })], hidden: [],
    });
    const sheet = await dialog.boundingBox();
    expect(sheet!.y).toBeGreaterThanOrEqual(0);
    expect(sheet!.y + sheet!.height).toBeLessThanOrEqual(keyboard ? 511 : page.viewportSize()!.height + 1);
    if (scene.state === "blocked") {
      await dialog.getByText(reason, { exact: true }).scrollIntoViewIfNeeded();
      await expect(dialog.getByText(reason, { exact: true })).toBeInViewport();
    }
    if (scene.held) {
      await dialog.getByText(hold, { exact: true }).scrollIntoViewIfNeeded();
      await walk.state(`${prefix}-full-hold`, { visible: [dialog.getByText(hold, { exact: true })], hidden: [] });
      await expect(dialog.getByText(hold, { exact: true })).toBeInViewport();
    }
    if (question) {
      await expect(dialog.getByRole("link", { name: "View question" })).toBeVisible();
      await expect(dialog.getByRole("button", { name: "Resume", exact: true })).toBeHidden();
    } else if (scene.state === "blocked" && phone) await expect(dialog.getByRole("button", { name: "Resume", exact: true })).toBeVisible();
    await walk.state(`${prefix}-restored`, { action: () => page.keyboard.press("Escape"), visible: [field, opener], hidden: [dialog] });
    await expect(opener).toBeFocused();
    await expect(field).toHaveValue("Keep this unsent draft and its cursor.");
    expect(await field.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([5, 9]);
    await expect(latest).toBeVisible();
    await expect(readingAnchor).toBeInViewport();
    expect(await anchorOffset()).toBeCloseTo(readingOffset, 0);
    expect(await scroller.evaluate((node) => node.scrollTop)).toBeCloseTo(readingTop, 0);
  }
  if (phone) {
    await field.focus();
    await page.evaluate(() => {
      Object.defineProperty(window.visualViewport!, "height", { configurable: true, value: 844 });
      window.visualViewport!.dispatchEvent(new Event("resize"));
    });
    await walk.state("04-keyboard-dismissed", { visible: [page.getByRole("navigation", { name: "Primary" }), field], hidden: [dialog] });
    await expect(field).toBeFocused();
    await expect(field).toHaveValue("Keep this unsent draft and its cursor.");
  }
  if (question) {
    await opener.click();
    await walk.state("05-view-question", {
      action: () => dialog.getByRole("link", { name: "View question" }).click(),
      visible: [conversation.locator(`[data-question-id="${question.id}"]`)], hidden: [dialog],
    });
    await expect(conversation.locator(".conversation-question")).toBeFocused();
    await expect(conversation.locator(`[data-question-id="${question.id}"]`)).toBeInViewport();
  }
  if (phone && scene.key === "running-held") {
    await page.getByRole("button", { name: "Latest messages", exact: true }).click();
    await field.focus();
    for (const height of [772, 510, 844]) {
      await page.evaluate((value) => {
        Object.defineProperty(window.visualViewport!, "height", { configurable: true, value });
        window.visualViewport!.dispatchEvent(new Event("resize"));
      }, height);
      await expect.poll(() => scroller.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThanOrEqual(1);
    }
    await walk.state("06-latest-after-toolbar-keyboard-dismissal", { visible: [field, page.getByRole("navigation", { name: "Primary" })], hidden: [page.getByRole("button", { name: "Latest messages", exact: true })] });
  }
  info.annotations.push({ type: "keyboard evidence", description: "VisualViewport contraction is simulated; this does not establish native mobile keyboard behavior." });
});
