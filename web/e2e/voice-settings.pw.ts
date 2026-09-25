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
  const voiceRow = main.getByRole("link", { name: "Voice input Your speech service · speech.example.test", exact: true });
  await walk.open(project.path);
  await draft.fill("Keep my typed project draft");
  await page.getByRole("button", { name: "More actions" }).click();
  await walk.state("01-settings-menu-entry", {
    visible: [page.getByRole("menuitem", { name: "Settings…", exact: true })], hidden: [voiceRow],
  });
  await walk.state("02-compact-overview", {
    action: () => page.getByRole("menuitem", { name: "Settings…", exact: true }).click(),
    visible: [voiceRow, main.getByRole("region", { name: "Network" })],
    hidden: [page.getByRole("radio"), page.getByLabel("Service URL")],
  });
  await voiceRow.focus();
  await walk.state("03-keyboard-opens-voice-options", {
    action: () => page.keyboard.press("Enter"),
    visible: [main.getByRole("radio", { name: "Browser recognition", exact: true }), main.getByRole("radio", { name: "Your speech service", exact: true }), page.getByLabel("Service URL")],
    hidden: [voiceRow, main.getByRole("region", { name: "Network" }), page.getByLabel("API key (optional)")],
  });
  await expect(main.getByRole("radio", { name: "Your speech service", exact: true })).toBeChecked();
  await page.getByLabel("Service URL").fill("https://unsaved.example.test/transcribe");
  await walk.state("04-unsaved-service", {
    visible: [page.getByLabel("Service URL"), page.getByRole("button", { name: "Save service" }), main.getByText("Changes are saved only with Save service.")], hidden: [voiceRow],
  });
  await walk.state("05-back-discards-unsaved-service", {
    action: () => main.getByRole("link", { name: "‹ Settings", exact: true }).click(),
    visible: [voiceRow], hidden: [page.getByLabel("Service URL"), page.getByRole("radio")],
  });
  expect((await savedVoice(request)).url).toBe("https://speech.example.test/v1/audio/transcriptions");
  if (info.project.name === "phone") await main.getByRole("button", { name: "‹ Back", exact: true }).click();
  else await page.getByRole("link", { name: project.name, exact: true }).click();
  await expect(draft).toHaveValue("Keep my typed project draft");
  if (info.project.name === "desktop") {
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await expect(voiceRow).toBeVisible();
  }
});

test("Voice settings persist backend choices and write-only hosted credentials", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const browser = main.getByRole("radio", { name: "Browser recognition", exact: true });
  const service = main.getByRole("radio", { name: "Your speech service", exact: true });
  const url = page.getByLabel("Service URL");
  const model = page.getByLabel("Model (optional)");
  const key = page.getByLabel("API key (optional)");
  const hosted = page.getByRole("button", { name: "Hosted provider? Add a key or model", exact: true });
  const howTo = main.getByRole("link", { name: "How to run one", exact: true });
  const save = page.getByRole("button", { name: "Save service" });
  const keySet = page.getByText("Key set · never shown", { exact: true });
  await walk.open("/settings/voice");
  await browser.click();
  await expect.poll(async () => (await savedVoice(request)).backend).toBe("browser");
  await expect(browser).toBeChecked();
  await walk.state("01-browser-saved", { visible: [page.getByRole("status").filter({ hasText: "Saved." }), browser], hidden: [url] });
  await service.check();
  await walk.state("02-service-asks-only-for-its-url", { visible: [url, hosted, howTo, save], hidden: [model, key, page.getByRole("status")] });
  await expect(howTo).toHaveAttribute("href", /\/docs\/OPERATIONS\.md#your-speech-service$/);
  expect((await savedVoice(request)).backend).toBe("browser");
  await url.fill("https://speech.example.test/v1/audio/transcriptions");
  await walk.state("03-hosted-link-reveals-key-and-model", { action: () => hosted.click(), visible: [url, model, key, save], hidden: [hosted] });
  await key.fill("fictional-settings-key");
  await save.click();
  await expect(keySet).toBeVisible();
  expect(await savedVoice(request)).toMatchObject({ backend: "endpoint", url: "https://speech.example.test/v1/audio/transcriptions", model: "whisper-1", key_set: true });
  await walk.state("04-service-saved-key-redacted", { visible: [keySet, page.getByRole("button", { name: "Replace", exact: true }), url], hidden: [key] });
  await page.reload();
  await expect(keySet).toBeVisible();
  await model.fill("fixture-model");
  await save.click();
  await expect.poll(async () => (await savedVoice(request)).model).toBe("fixture-model");
  expect((await savedVoice(request)).key_set).toBe(true);
  await page.getByRole("button", { name: "Replace", exact: true }).click();
  await expect(key).toHaveValue("");
  await walk.state("05-replace-key-with-empty-removes-it", { visible: [key, page.getByText("Leave blank to remove the stored key.", { exact: false })], hidden: [keySet] });
  await save.click();
  await expect.poll(async () => (await savedVoice(request)).key_set).toBe(false);
  await expect(model).toHaveValue("fixture-model"); // a saved custom model keeps the hosted fields open
  await key.fill("fictional-settings-key");
  await save.click();
  await expect(keySet).toBeVisible();
  await url.fill("https://other.example.test/v1/audio/transcriptions");
  await walk.state("06-changed-destination-does-not-retain-key", { visible: [key, save], hidden: [keySet] });
  await expect(key).toHaveValue("");
  await save.click();
  await expect.poll(async () => (await savedVoice(request)).url).toBe("https://other.example.test/v1/audio/transcriptions");
  expect((await savedVoice(request)).key_set).toBe(false);
  await main.getByRole("link", { name: "‹ Settings", exact: true }).click();
  await expect(main.getByRole("link", { name: "Voice input Your speech service · other.example.test", exact: true })).toBeVisible();
  await page.reload();
  await expect(main.getByRole("link", { name: "Voice input Your speech service · other.example.test", exact: true })).toBeVisible();
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
    const service = page.getByRole("radio", { name: "Your speech service", exact: true });
    await expect(service).toBeChecked();
    await browser.click();
    await walk.state("03-saving-controls-disabled", { visible: [page.getByText("Saving…", { exact: true }), service], hidden: [page.getByRole("alert")] });
    await expect(browser).toBeDisabled();
    await expect(service).toBeDisabled();
    releaseSave();
    await walk.state("04-denied-keeps-saved-choice", { visible: [page.getByRole("alert").filter({ hasText: "Fixture settings update denied" }), page.getByRole("button", { name: "Retry", exact: true })], hidden: [page.getByText("Saving…", { exact: true })] });
    expect((await savedVoice(request)).backend).toBe("endpoint");
    await expect(service).toBeChecked();
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
  await expect(page.getByRole("radio", { name: "Your speech service", exact: true })).toBeChecked();
  await page.getByRole("link", { name: "Needs you", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Needs you", exact: true })).toBeVisible();
  await expect(page.getByRole("radio", { name: "Your speech service", exact: true })).toBeHidden();
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
    await expect(page.getByLabel("Service URL")).toHaveValue("https://speech.example.test/v1/audio/transcriptions");
    release();
    await expect(page.getByLabel("Service URL")).toHaveValue("https://fresh.example.test/transcribe");
    await expect(page.getByLabel("Model (optional)")).toHaveValue("fresh-model");
    await walk.state("01-fresh-choice-replaces-cached-form", {
      visible: [page.getByLabel("Service URL"), page.getByLabel("Model (optional)"), page.getByRole("button", { name: "Save service" })],
      hidden: [page.getByRole("alert")],
    });
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
});
