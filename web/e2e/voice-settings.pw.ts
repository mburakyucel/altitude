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

/** A transcription card on the voice page, and its In use mark. */
function card(page: Page, title: string) {
  return page.getByRole("main").locator(".voice-option").filter({ has: page.getByRole("heading", { name: title, exact: true }) });
}
const inUse = (page: Page, title: string) => card(page, title).getByText("In use", { exact: true });
const use = (page: Page, name: string) => page.getByRole("main").getByRole("button", { name, exact: true });

/** The real settings responses, with this computer's voice reported ready so it can be chosen. */
async function readyHost(page: Page) {
  await page.route("**/api/voice", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), host: { state: "ready", download_bytes: 698435338 } } });
  });
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
  const hostInUse = inUse(page, "This computer");
  const browser = use(page, "Use browser recognition");
  await walk.open(project.path);
  await draft.fill("Keep my typed project draft");
  await page.getByRole("button", { name: "More actions" }).click();
  await walk.state("01-settings-menu-entry", {
    visible: [page.getByRole("menuitem", { name: "All settings…", exact: true })], hidden: [row],
  });
  await walk.state("02-compact-overview", {
    action: () => page.getByRole("menuitem", { name: "All settings…", exact: true }).click(),
    visible: [row, main.getByRole("region", { name: "Devices and access", exact: true })],
    hidden: [browser],
  });
  await row.focus();
  await walk.state("03-keyboard-opens-voice-options", {
    action: () => page.keyboard.press("Enter"),
    visible: [hostInUse, browser, use(page, "Use your speech service")],
    hidden: [row, main.getByRole("region", { name: "Devices and access", exact: true })],
  });
  await expect(main.getByRole("heading", { level: 3 })).toHaveText(["This computer", "Browser recognition", "Your speech service"]);
  await walk.state("04-back-returns-to-overview", {
    action: () => main.getByRole("link", { name: "‹ Settings", exact: true }).click(),
    visible: [row], hidden: [browser],
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
  await readyHost(page);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const saved = page.getByRole("status").filter({ hasText: "Saved." });
  await walk.open("/settings/voice");
  await expect(inUse(page, "This computer")).toBeVisible();
  await walk.state("01-browser-saved", {
    action: () => use(page, "Use browser recognition").click(),
    visible: [inUse(page, "Browser recognition"), use(page, "Use this computer")],
    hidden: [inUse(page, "This computer"), use(page, "Use browser recognition"), page.getByRole("alert")],
  });
  await expect(saved).toHaveCount(1);
  await expect.poll(async () => (await savedVoice(request)).backend).toBe("browser");
  await page.reload();
  await expect(inUse(page, "Browser recognition")).toBeVisible();
  await main.getByRole("link", { name: "‹ Settings", exact: true }).click();
  const browserRow = main.getByRole("link", { name: "Voice input Browser recognition", exact: true });
  await walk.state("02-row-says-browser", { visible: [browserRow], hidden: [use(page, "Use this computer")] });
  await browserRow.click();
  await walk.state("03-this-computer-saved", {
    action: () => use(page, "Use this computer").click(),
    visible: [inUse(page, "This computer"), use(page, "Use browser recognition")],
    hidden: [use(page, "Use this computer"), page.getByRole("alert")],
  });
  await expect.poll(async () => (await savedVoice(request)).backend).toBe("host");
  await main.getByRole("link", { name: "‹ Settings", exact: true }).click();
  await expect(main.getByRole("link", { name: "Voice input This computer", exact: true })).toBeVisible();
  await page.reload();
  await expect(main.getByRole("link", { name: "Voice input This computer", exact: true })).toBeVisible();
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
    await walk.state("01-loading-no-default-selected", { visible: [page.getByRole("status", { name: "Loading settings…", exact: true })], hidden: [page.getByText("In use", { exact: true })] });
    releaseRead();
    await expect(page.getByRole("alert").filter({ hasText: "Could not load settings" })).toBeVisible({ timeout: 15_000 });
    await walk.state("02-reading-failed-retry", { visible: [page.getByRole("alert").filter({ hasText: "Could not load settings" }), page.getByRole("button", { name: "Retry", exact: true })], hidden: [page.getByText("In use", { exact: true }), page.getByRole("status", { name: "Loading settings…", exact: true })] });
    failRead = false;
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    const browser = card(page, "Browser recognition");
    await expect(inUse(page, "This computer")).toBeVisible();
    await use(page, "Use browser recognition").click();
    await walk.state("03-saving-controls-locked", { visible: [browser.getByRole("button", { name: "Saving…" }), inUse(page, "This computer")], hidden: [page.getByRole("alert")] });
    await expect(use(page, "Use your speech service")).toHaveAttribute("aria-disabled", "true");
    releaseSave();
    await walk.state("04-denied-keeps-saved-choice", { visible: [browser.getByRole("alert").filter({ hasText: "Fixture settings update denied" }), page.getByRole("button", { name: "Retry", exact: true }), inUse(page, "This computer")], hidden: [browser.getByRole("button", { name: "Saving…" })] });
    expect((await savedVoice(request)).backend).toBe("host");
    failSave = false;
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect.poll(async () => (await savedVoice(request)).backend).toBe("browser");
    await walk.state("05-retry-saves-through-real-handler", { visible: [inUse(page, "Browser recognition")], hidden: [page.getByRole("alert"), inUse(page, "This computer")] });
  } finally {
    releaseRead();
    releaseSave();
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("A choice made from a stale page is refused and Reload settings shows the current one", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/settings/voice");
  await expect(inUse(page, "This computer")).toBeVisible();
  // Another device switches to browser recognition while this page still shows this computer.
  const current = await savedVoice(request);
  expect((await request.post("/api/voice", { data: { backend: "browser", selection: current.selection } })).ok()).toBe(true);
  await expect(inUse(page, "This computer")).toBeVisible();
  const stale = card(page, "Browser recognition").getByRole("alert").filter({ hasText: "Voice settings changed." });
  const reload = page.getByRole("button", { name: "Reload settings", exact: true });
  // The page asks for browser recognition too, with the selection it read: the host refuses it (409).
  await walk.state("01-stale-choice-refused", {
    action: () => use(page, "Use browser recognition").click(),
    visible: [stale, reload, inUse(page, "This computer")],
    hidden: [inUse(page, "Browser recognition")],
  });
  await expect(page.getByRole("button", { name: "Retry", exact: true })).toHaveCount(0);
  await walk.state("02-reload-shows-current-choice", {
    action: () => reload.click(),
    visible: [inUse(page, "Browser recognition")],
    hidden: [stale, reload, inUse(page, "This computer")],
  });
  expect((await savedVoice(request)).backend).toBe("browser");
});

test("Reopening voice settings replaces the cached page with a fresh machine choice", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const host = inUse(page, "This computer");
  const browser = inUse(page, "Browser recognition");
  await walk.open("/settings/voice");
  await expect(host).toBeVisible();
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
    await expect(host).toBeVisible();
    release();
    await expect(browser).toBeVisible();
    await walk.state("01-fresh-choice-replaces-cached-form", {
      visible: [browser],
      hidden: [host, page.getByRole("alert")],
    });
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
});
