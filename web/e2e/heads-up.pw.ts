import { test } from "./fixtures";
import { expect } from "@playwright/test";
import { walkthrough } from "./walkthrough";

const route = "/projects/atlas";
const text = /Deliveries are waiting on the build\.\s+An owner is investigating the failure\./;

test.setTimeout(120_000); // Idle conversations poll every 20 seconds; failed reads retain normal retries.

test("selected L3 heads-up stays between routine groups, with evidence and task navigation", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await walk.open(route);
  const convo = page.getByRole("region", { name: "Conversation", exact: true });
  const headsUp = convo.locator(".sys-line", { hasText: text });
  await expect(convo.getByText("The fixture records the chosen scope.")).toBeVisible();
  await expect(headsUp).toBeHidden();
  const response = await request.post("/fixture/heads-up", { data: {} });
  expect(response.ok()).toBe(true);
  const row = await response.json();
  expect(row).toMatchObject({ by: "l3", heads_up: true, role: "system", trigger: "fyi" });
  const before = convo.locator('.sys-line[data-group="2"]').last();
  const after = convo.locator('.sys-line[data-group="3"]');
  await expect(headsUp).toBeVisible({ timeout: 30_000 });
  await walk.state("01-selected-arrival-routine-folded", {
    visible: [headsUp, before, after, convo.getByText("The fixture records the chosen scope.")],
    hidden: [convo.getByText("Activation pending: routine build details."), convo.getByText("Automatic fault evidence stays here.")],
  });
  expect(await headsUp.locator(".sys-text").innerText()).toMatch(text);
  const scroll = convo.locator(".convo-scroll");
  await expect.poll(() => scroll.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThanOrEqual(1);
  await expect(headsUp).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  const beforeList = convo.getByRole("group", { name: "2 system events" });
  await walk.state("02-routine-evidence-expanded", {
    action: () => before.getByRole("button", { name: "Show", exact: true }).click(),
    visible: [headsUp, beforeList.getByText(/Activation pending: routine build details\./), beforeList.getByText(/The saved tasks have been checked\./)],
    hidden: [],
  });
  await walk.state("03-routine-evidence-folded", {
    action: () => beforeList.getByRole("button", { name: "Hide", exact: true }).click(),
    visible: [headsUp, before], hidden: [beforeList],
  });
  const afterList = convo.getByRole("group", { name: "3 system events" });
  await walk.state("04-automatic-and-historical-evidence", {
    action: () => after.getByRole("button", { name: "Show", exact: true }).click(),
    visible: [headsUp, afterList.getByText(/Automatic fault evidence/), afterList.getByText(/Historical automatic FYI/), afterList.getByText(/Routine owner progress/)],
    hidden: [after],
  });
  await afterList.getByRole("button", { name: "Hide", exact: true }).click();
  const card = convo.getByRole("article", { name: "FYI · Prepare index migration" });
  await walk.state("05-heads-up-card", {
    action: () => headsUp.getByRole("button", { name: "Show", exact: true }).click(),
    visible: [card, card.getByText("Deliveries are waiting on the build."), card.getByText("An owner is investigating the failure."), card.getByRole("link", { name: "Open task" })],
    hidden: [headsUp, afterList],
  });
  await walk.state("06-heads-up-restored", {
    action: () => card.getByRole("button", { name: "Hide", exact: true }).click(),
    visible: [headsUp, after], hidden: [card],
  });
  await headsUp.getByRole("button", { name: "Show", exact: true }).click();
  await walk.state("07-owning-task", {
    action: () => card.getByRole("link", { name: "Open task" }).click(),
    visible: [page.getByRole("heading", { name: "Prepare index migration", exact: true })],
    hidden: [card, headsUp],
  });
});

test("heads-up empty, loading, read failure, retry and cached-error states", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  let mode: "loading" | "empty" | "failed" | "real" = "loading";
  let release = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route((url) => url.pathname === "/api/chat/atlas", async (intercept) => {
    if (mode === "loading") await gate;
    if (mode === "empty") return intercept.fulfill({ json: { history: [], active: null, busy: false, queued: [] } });
    if (mode === "failed") return intercept.fulfill({ status: 500, json: { error: "Fictional read failure" } });
    return intercept.continue();
  });
  await walk.open(route);
  const convo = page.getByRole("region", { name: "Conversation", exact: true });
  const headsUp = convo.locator(".sys-line", { hasText: text });
  const loading = convo.getByLabel("Loading", { exact: true });
  const empty = convo.getByText("Say what you want done. L3 answers or creates one task.");
  const error = convo.getByRole("alert");
  await walk.state("01-loading-overlay", { visible: [loading], hidden: [headsUp, empty, error] });
  mode = "empty";
  release();
  await walk.state("02-empty-overlay", { visible: [empty], hidden: [loading, headsUp, convo.locator(".sys-line")] });
  mode = "failed";
  await walk.open(route);
  await expect(error).toBeVisible({ timeout: 30_000 });
  await walk.state("03-read-error-overlay", {
    visible: [error, error.getByRole("button", { name: "Retry" })], hidden: [headsUp, loading],
  });
  expect((await request.post("/fixture/heads-up", { data: {} })).ok()).toBe(true);
  mode = "real";
  await walk.state("04-retry-loaded", {
    action: () => error.getByRole("button", { name: "Retry" }).click(),
    visible: [headsUp], hidden: [error, loading, empty],
  });
  mode = "failed";
  await expect(error).toBeVisible({ timeout: 30_000 });
  await walk.state("05-cached-heads-up-during-error", { visible: [error, headsUp], hidden: [loading, empty] });
  mode = "real";
  await walk.state("06-recovered", {
    action: () => error.getByRole("button", { name: "Retry" }).click(),
    visible: [headsUp], hidden: [error],
  });
  await page.unrouteAll({ behavior: "wait" });
});

test("a heads-up arriving below a reader preserves scroll position", async ({ page, request }, info) => {
  expect((await request.post("/fixture/heads-up", { data: { history: true } })).ok()).toBe(true);
  const walk = walkthrough(page, info);
  await walk.open(route);
  const convo = page.getByRole("region", { name: "Conversation", exact: true });
  const scroll = convo.locator(".convo-scroll");
  // Read back only once the conversation has opened at its end.
  await expect(convo.locator(".sys-line", { hasText: text })).toBeInViewport();
  await scroll.evaluate((node) => { node.scrollTop = 120; node.dispatchEvent(new Event("scroll", { bubbles: true })); });
  const top = await scroll.evaluate((node) => node.scrollTop);
  await walk.state("01-reading-history", { visible: [convo], hidden: [] });
  expect((await request.post("/fixture/heads-up", { data: {} })).ok()).toBe(true);
  await expect(convo.locator(".sys-line", { hasText: text })).toHaveCount(2, { timeout: 30_000 });
  expect(await scroll.evaluate((node) => node.scrollTop)).toBe(top);
  await walk.state("02-arrival-keeps-reader-position", { visible: [convo], hidden: [] });
  await scroll.evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await expect(convo.locator(".sys-line", { hasText: text }).last()).toBeInViewport();
  await walk.state("03-latest-heads-up", { visible: [convo.locator(".sys-line", { hasText: text }).last()], hidden: [] });
});
