import { expect, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

// Real settings reads/writes in the disposable service. These tests never record audio or contact
// the configured destinations; .test URLs and the key below are fictional fixture values.
async function savedVoice(request: APIRequestContext) {
  const response = await request.get("/api/voice");
  expect(response.ok()).toBe(true);
  const value = await response.json();
  expect(value).not.toHaveProperty("key");
  expect(JSON.stringify(value)).not.toContain("fictional-settings-key");
  return value;
}

test("Settings navigation keeps voice options nested and preserves the project draft", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const draft = main.getByRole("textbox", { name: /^Message L3 about /, includeHidden: true });
  const voiceRow = main.getByRole("link", { name: /Voice input Local speech service/ });
  await walk.open(project.path);
  await draft.fill("Keep my typed project draft");
  await page.getByRole("button", { name: "More actions" }).click();
  await walk.state("01-settings-menu-entry", {
    visible: [page.getByRole("menuitem", { name: "Settings…", exact: true })], hidden: [voiceRow],
  });
  await walk.state("02-compact-overview", {
    action: () => page.getByRole("menuitem", { name: "Settings…", exact: true }).click(),
    visible: [voiceRow, main.getByRole("region", { name: "Network" })],
    hidden: [page.getByRole("radio"), page.getByLabel("Endpoint URL")],
  });
  await voiceRow.focus();
  await walk.state("03-keyboard-opens-voice-options", {
    action: () => page.keyboard.press("Enter"),
    visible: [main.getByRole("radio", { name: "Browser recognition", exact: true }), main.getByRole("radio", { name: "Local speech service", exact: true }), main.getByRole("radio", { name: "Custom endpoint", exact: true })],
    hidden: [voiceRow, main.getByRole("region", { name: "Network" }), page.getByLabel("Endpoint URL")],
  });
  await expect(main.getByRole("radio", { name: "Local speech service", exact: true })).toBeChecked();
  await main.getByRole("radio", { name: "Custom endpoint", exact: true }).check();
  await page.getByLabel("Endpoint URL").fill("https://unsaved.example.test/transcribe");
  await walk.state("04-unsaved-endpoint", {
    visible: [page.getByLabel("Endpoint URL"), page.getByRole("button", { name: "Save endpoint" }), main.getByText("Changes are saved only with Save endpoint.")], hidden: [voiceRow],
  });
  await walk.state("05-back-discards-unsaved-endpoint", {
    action: () => main.getByRole("link", { name: "‹ Settings", exact: true }).click(),
    visible: [voiceRow], hidden: [page.getByLabel("Endpoint URL"), page.getByRole("radio")],
  });
  expect((await savedVoice(request)).backend).toBe("local");
  if (info.project.name === "phone") await main.getByRole("button", { name: "‹ Back", exact: true }).click();
  else await page.getByRole("link", { name: project.name, exact: true }).click();
  await expect(draft).toHaveValue("Keep my typed project draft");
  if (info.project.name === "desktop") {
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await expect(voiceRow).toBeVisible();
  }
});

test("Voice settings persist backend choices and write-only endpoint credentials", async ({ page, request }, info) => {
  // Acknowledged saves must accept the next edit even while batched cache notifications lag.
  // This exposes the post-merge key-clearing race without changing handlers or extending waits.
  await page.addInitScript(() => {
    const schedule = window.setTimeout.bind(window);
    window.setTimeout = (handler, delay, ...args) => schedule(handler, delay === 0 ? 250 : delay, ...args);
  });
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const browser = main.getByRole("radio", { name: "Browser recognition", exact: true });
  const local = main.getByRole("radio", { name: "Local speech service", exact: true });
  const endpoint = main.getByRole("radio", { name: "Custom endpoint", exact: true });
  const url = page.getByLabel("Endpoint URL");
  const model = page.getByLabel("Model (optional)");
  const key = page.getByLabel("API key (optional)");
  const save = page.getByRole("button", { name: "Save endpoint" });
  const keySet = page.getByText("Key set · never shown", { exact: true });
  await walk.open("/settings/voice");
  await browser.click();
  await expect.poll(async () => (await savedVoice(request)).backend).toBe("browser");
  await expect(browser).toBeChecked();
  await walk.state("01-browser-saved", { visible: [page.getByRole("status").filter({ hasText: "Saved." }), browser], hidden: [url] });
  await local.click();
  await expect.poll(async () => (await savedVoice(request)).backend).toBe("local");
  await expect(local).toBeChecked();
  await walk.state("02-local-saved", { visible: [page.getByRole("status").filter({ hasText: "Saved." }), local], hidden: [url] });
  await endpoint.check();
  await url.fill("https://speech.example.test/v1/audio/transcriptions");
  await key.fill("fictional-settings-key");
  await save.click();
  await expect(keySet).toBeVisible();
  expect(await savedVoice(request)).toMatchObject({ backend: "endpoint", url: "https://speech.example.test/v1/audio/transcriptions", model: "whisper-1", key_set: true });
  await walk.state("03-endpoint-saved-key-redacted", { visible: [keySet, page.getByRole("button", { name: "Replace", exact: true }), url], hidden: [key] });
  await page.reload();
  await expect(keySet).toBeVisible();
  await model.fill("fixture-model");
  await save.click();
  await expect.poll(async () => (await savedVoice(request)).model).toBe("fixture-model");
  expect((await savedVoice(request)).key_set).toBe(true);
  await page.getByRole("button", { name: "Replace", exact: true }).click();
  await expect(key).toHaveValue("");
  await walk.state("04-replace-key-with-empty-removes-it", { visible: [key, page.getByText("Leave blank to remove the stored key.", { exact: false })], hidden: [keySet] });
  await save.click();
  await expect.poll(async () => (await savedVoice(request)).key_set).toBe(false);
  await key.fill("fictional-settings-key");
  await save.click();
  await expect(keySet).toBeVisible();
  await url.fill("https://other.example.test/v1/audio/transcriptions");
  await walk.state("05-changed-destination-does-not-retain-key", { visible: [key, save], hidden: [keySet] });
  await expect(key).toHaveValue("");
  await save.click();
  await expect.poll(async () => (await savedVoice(request)).url).toBe("https://other.example.test/v1/audio/transcriptions");
  expect((await savedVoice(request)).key_set).toBe(false);
  await main.getByRole("link", { name: "‹ Settings", exact: true }).click();
  await expect(main.getByRole("link", { name: /Voice input Custom endpoint/ })).toBeVisible();
  await page.reload();
  await expect(main.getByRole("link", { name: /Voice input Custom endpoint/ })).toBeVisible();
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
    const local = page.getByRole("radio", { name: "Local speech service", exact: true });
    await expect(local).toBeChecked();
    await browser.click();
    await walk.state("03-saving-controls-disabled", { visible: [page.getByText("Saving…", { exact: true }), local], hidden: [page.getByRole("alert")] });
    await expect(browser).toBeDisabled();
    await expect(local).toBeDisabled();
    releaseSave();
    await walk.state("04-denied-keeps-saved-choice", { visible: [page.getByRole("alert").filter({ hasText: "Fixture settings update denied" }), page.getByRole("button", { name: "Retry", exact: true })], hidden: [page.getByText("Saving…", { exact: true })] });
    expect((await savedVoice(request)).backend).toBe("local");
    await expect(local).toBeChecked();
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

test("Reopening voice settings replaces the cached form with a fresh machine choice", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/settings/voice");
  await expect(page.getByRole("radio", { name: "Local speech service", exact: true })).toBeChecked();
  await page.getByRole("link", { name: "Needs you", exact: true }).click();
  const current = await savedVoice(request);
  const changed = await request.post("/api/voice", { data: {
    backend: "endpoint", selection: current.selection,
    url: "https://fresh.example.test/transcribe", model: "fresh-model",
  } });
  expect(changed.ok()).toBe(true);
  let release = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/voice", async (route) => {
    if (route.request().method() === "GET") await gate;
    return route.continue();
  });
  try {
    await page.goBack();
    await expect(page.getByRole("radio", { name: "Local speech service", exact: true })).toBeChecked();
    release();
    await expect(page.getByRole("radio", { name: "Custom endpoint", exact: true })).toBeChecked();
    await expect(page.getByLabel("Endpoint URL")).toHaveValue("https://fresh.example.test/transcribe");
    await expect(page.getByLabel("Model (optional)")).toHaveValue("fresh-model");
    await walk.state("01-fresh-choice-replaces-cached-form", {
      visible: [page.getByLabel("Endpoint URL"), page.getByRole("button", { name: "Save endpoint" })],
      hidden: [page.getByRole("alert")],
    });
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
});
