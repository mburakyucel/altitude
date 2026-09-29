import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

// Real settings reads/writes in the disposable service, which starts on host voice. These tests never
// record audio or set up the speech model: host voice's setup state is whatever this machine reports.
async function savedVoice(request: APIRequestContext) {
  const response = await request.get("/api/voice");
  expect(response.ok()).toBe(true);
  return response.json();
}

/** The Settings row for voice input, named by its summary of the saved choice. */
async function voiceRow(page: Page, request: APIRequestContext) {
  const voice = await savedVoice(request);
  const summary = voice.backend === "browser" ? "Browser recognition"
    : voice.host.state === "ready" ? "This computer" : "This computer · not set up";
  return page.getByRole("main").getByRole("link", { name: `Voice input ${summary}`, exact: true });
}

test("Settings navigation keeps voice options nested and preserves the project draft", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const draft = main.getByRole("textbox", { name: /^Message L3 about /, includeHidden: true });
  const row = await voiceRow(page, request);
  const host = main.getByRole("radio", { name: "This computer", exact: true });
  const browser = main.getByRole("radio", { name: "Browser recognition", exact: true });
  await walk.open(project.path);
  await draft.fill("Keep my typed project draft");
  await page.getByRole("button", { name: "More actions" }).click();
  await walk.state("01-settings-menu-entry", {
    visible: [page.getByRole("menuitem", { name: "Settings…", exact: true })], hidden: [row],
  });
  await walk.state("02-compact-overview", {
    action: () => page.getByRole("menuitem", { name: "Settings…", exact: true }).click(),
    visible: [row, main.getByRole("region", { name: "Network" })],
    hidden: [page.getByRole("radio")],
  });
  await row.focus();
  await walk.state("03-keyboard-opens-voice-options", {
    action: () => page.keyboard.press("Enter"),
    visible: [host, browser],
    hidden: [row, main.getByRole("region", { name: "Network" })],
  });
  await expect(page.getByRole("radio")).toHaveCount(2);
  await expect(host).toBeChecked();
  await expect(browser).not.toBeChecked();
  await walk.state("04-back-returns-to-overview", {
    action: () => main.getByRole("link", { name: "‹ Settings", exact: true }).click(),
    visible: [row], hidden: [page.getByRole("radio")],
  });
  expect((await savedVoice(request)).backend).toBe("host");
  if (info.project.name === "phone") await main.getByRole("button", { name: "‹ Back", exact: true }).click();
  else await page.getByRole("link", { name: project.name, exact: true }).click();
  await expect(draft).toHaveValue("Keep my typed project draft");
  if (info.project.name === "desktop") {
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await expect(row).toBeVisible();
  }
});

test("Voice settings persist the choice between this computer and browser recognition", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const host = main.getByRole("radio", { name: "This computer", exact: true });
  const browser = main.getByRole("radio", { name: "Browser recognition", exact: true });
  const saved = page.getByRole("status").filter({ hasText: "Saved." });
  await walk.open("/settings/voice");
  await expect(host).toBeChecked();
  await walk.state("01-browser-saved", { action: () => browser.click(), visible: [saved, browser], hidden: [page.getByRole("alert")] });
  await expect.poll(async () => (await savedVoice(request)).backend).toBe("browser");
  await expect(browser).toBeChecked();
  await expect(host).not.toBeChecked();
  await page.reload();
  await expect(browser).toBeChecked();
  await main.getByRole("link", { name: "‹ Settings", exact: true }).click();
  const browserRow = main.getByRole("link", { name: "Voice input Browser recognition", exact: true });
  await walk.state("02-row-says-browser", { visible: [browserRow], hidden: [page.getByRole("radio")] });
  await browserRow.click();
  await walk.state("03-this-computer-saved", { action: () => host.click(), visible: [saved, host], hidden: [page.getByRole("alert")] });
  await expect.poll(async () => (await savedVoice(request)).backend).toBe("host");
  await expect(host).toBeChecked();
  await main.getByRole("link", { name: "‹ Settings", exact: true }).click();
  await expect(await voiceRow(page, request)).toBeVisible();
  await page.reload();
  await expect(await voiceRow(page, request)).toBeVisible();
});

test("Voice settings loading, denied save and retry keep the persisted selection", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  let releaseRead = () => {};
  const readGate = new Promise<void>((resolve) => { releaseRead = resolve; });
  let failRead = true;
  let failSave = true;
  let releaseSave = () => {};
  const saveGate = new Promise<void>((resolve) => { releaseSave = resolve; });
  await page.route("**/api/voice", async (route) => {
    if (route.request().method() === "GET" && failRead) {
      await readGate;
      return route.fulfill({ status: 503, json: { error: "Fixture settings temporarily unavailable" } });
    }
    if (route.request().method() === "POST" && failSave) {
      await saveGate;
      return route.fulfill({ status: 403, json: { error: "Fixture settings update denied" } });
    }
    return route.continue();
  });
  try {
    await walk.open("/settings/voice");
    await walk.state("01-loading-no-default-selected", { visible: [page.getByText("Loading settings…", { exact: true })], hidden: [page.getByRole("radio")] });
    releaseRead();
    await expect(page.getByRole("alert").filter({ hasText: "Could not load settings" })).toBeVisible({ timeout: 15_000 });
    await walk.state("02-reading-failed-retry", { visible: [page.getByRole("alert").filter({ hasText: "Could not load settings" }), page.getByRole("button", { name: "Retry", exact: true })], hidden: [page.getByRole("radio"), page.getByText("Loading settings…", { exact: true })] });
    failRead = false;
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    const browser = page.getByRole("radio", { name: "Browser recognition", exact: true });
    const host = page.getByRole("radio", { name: "This computer", exact: true });
    await expect(host).toBeChecked();
    await browser.click();
    await walk.state("03-saving-controls-disabled", { visible: [page.getByText("Saving…", { exact: true }), host], hidden: [page.getByRole("alert")] });
    await expect(browser).toBeDisabled();
    await expect(host).toBeDisabled();
    releaseSave();
    await walk.state("04-denied-keeps-saved-choice", { visible: [page.getByRole("alert").filter({ hasText: "Fixture settings update denied" }), page.getByRole("button", { name: "Retry", exact: true })], hidden: [page.getByText("Saving…", { exact: true })] });
    expect((await savedVoice(request)).backend).toBe("host");
    failSave = false;
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect.poll(async () => (await savedVoice(request)).backend).toBe("browser");
    await walk.state("05-retry-saves-through-real-handler", { visible: [page.getByText("Saved.", { exact: true }), browser], hidden: [page.getByRole("alert")] });
    await expect(browser).toBeChecked();
  } finally {
    releaseRead();
    releaseSave();
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("A choice made from a stale page is refused and Reload settings shows the current one", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const host = main.getByRole("radio", { name: "This computer", exact: true });
  const browser = main.getByRole("radio", { name: "Browser recognition", exact: true });
  await walk.open("/settings/voice");
  await expect(host).toBeChecked();
  // Another device switches to browser recognition while this page still shows this computer.
  const current = await savedVoice(request);
  expect((await request.post("/api/voice", { data: { backend: "browser", selection: current.selection } })).ok()).toBe(true);
  await expect(host).toBeChecked();
  const stale = page.getByRole("alert").filter({ hasText: "Voice settings changed. Reload settings and try again." });
  const reload = page.getByRole("button", { name: "Reload settings", exact: true });
  // The page asks for browser recognition too, with the selection it read: the host refuses it (409).
  await walk.state("01-stale-choice-refused", {
    action: () => browser.click(),
    visible: [stale, reload],
    hidden: [page.getByText("Saved.", { exact: true })],
  });
  await expect(page.getByRole("button", { name: "Retry", exact: true })).toHaveCount(0);
  await walk.state("02-reload-shows-current-choice", {
    action: () => reload.click(),
    visible: [browser],
    hidden: [stale, reload],
  });
  await expect(browser).toBeChecked();
  await expect(host).not.toBeChecked();
  expect((await savedVoice(request)).backend).toBe("browser");
});

test("Reopening voice settings replaces the cached form with a fresh machine choice", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const host = page.getByRole("radio", { name: "This computer", exact: true });
  const browser = page.getByRole("radio", { name: "Browser recognition", exact: true });
  await walk.open("/settings/voice");
  await expect(host).toBeChecked();
  await page.getByRole("link", { name: "Needs you", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Needs you", exact: true })).toBeVisible();
  await expect(host).toBeHidden();
  const current = await savedVoice(request);
  const changed = await request.post("/api/voice", { data: { backend: "browser", selection: current.selection } });
  expect(changed.ok()).toBe(true);
  let release = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/voice", async (route) => {
    if (route.request().method() === "GET") await gate;
    return route.continue();
  });
  try {
    await page.goBack();
    await expect(host).toBeChecked();
    release();
    await expect(browser).toBeChecked();
    await walk.state("01-fresh-choice-replaces-cached-form", {
      visible: [browser, host],
      hidden: [page.getByRole("alert")],
    });
    await expect(host).not.toBeChecked();
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
});
