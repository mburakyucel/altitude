import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "speech-service.py" });
const endpoint = "https://speech.example.test/v1/audio/transcriptions";
async function voice(request: APIRequestContext) {
  const response = await request.get("/api/voice");
  expect(response.ok()).toBe(true);
  return response.json();
}
async function configure(request: APIRequestContext, model = "") {
  const current = await voice(request);
  const response = await request.post("/api/voice", { data: { backend: "service", selection: current.selection, url: endpoint, model } });
  expect(response.ok()).toBe(true);
}
function controls(page: Page) {
  const main = page.getByRole("main");
  return {
    main,
    field: main.getByRole("textbox", { name: /^Message L3 about /, includeHidden: true }),
    mic: main.getByRole("button", { name: "Start voice input", exact: true }),
    stop: main.getByRole("button", { name: "Stop voice input", exact: true }),
    cancel: main.getByRole("button", { name: "Cancel voice input", exact: true }),
    send: main.getByRole("button", { name: "Send", exact: true }),
    listening: main.locator('.composer[data-phase="listening"]'),
  };
}

test("speech service settings: explicit save, key privacy, new address and removal", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/settings/voice");
  const service = page.getByRole("radio", { name: "Your speech service", exact: true });
  const url = page.getByRole("textbox", { name: "Transcription URL", exact: true });
  const key = page.getByLabel("API key", { exact: true });
  await service.click();
  await walk.state("service-settings-01-empty", { visible: [url, page.getByRole("button", { name: "Save and use service", exact: true })], hidden: [page.getByRole("alert")] });
  expect((await voice(request)).backend).toBe("host");
  await url.fill("http://speech.example.test/v1/audio/transcriptions");
  await walk.state("service-settings-02-http", { visible: [page.getByText("This connection is unencrypted. Audio and any key travel over HTTP.", { exact: true })], hidden: [] });
  await url.fill(endpoint);
  await page.getByText("Key or model (optional)", { exact: true }).click();
  await key.fill("fictional-test-key");
  await page.getByRole("textbox", { name: "Model", exact: true }).fill("fixture-model");
  await page.getByRole("button", { name: "Save and use service", exact: true }).click();
  await expect.poll(async () => (await voice(request)).backend).toBe("service");
  const saved = await voice(request);
  expect(saved.service).toEqual({ url: endpoint, model: "fixture-model", key_present: true });
  expect(JSON.stringify(saved)).not.toContain("fictional-test-key");
  await page.reload();
  await page.getByText("Key or model (optional)", { exact: true }).click();
  await expect(key).toHaveValue("");
  await walk.state("service-settings-03-saved-key", { visible: [page.getByRole("checkbox", { name: "Remove saved key", exact: true })], hidden: [page.getByRole("alert")] });
  await url.fill("https://speech.example.test/another/transcriptions");
  await walk.state("service-settings-04-new-address", { visible: [page.getByText("Enter a key for the new address, if needed.", { exact: true })], hidden: [] });
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(url).toHaveValue(endpoint);
  await page.getByRole("checkbox", { name: "Remove saved key", exact: true }).check();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect.poll(async () => (await voice(request)).service.key_present).toBe(false);
  await page.getByRole("radio", { name: "Browser recognition", exact: true }).click();
  await walk.state("service-settings-05-switch-is-a-draft", { visible: [page.getByRole("button", { name: "Save and remove service", exact: true })], hidden: [url] });
  expect((await voice(request)).backend).toBe("service");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(service).toBeChecked();
  await page.getByRole("radio", { name: "Browser recognition", exact: true }).click();
  await page.getByRole("button", { name: "Save and remove service", exact: true }).click();
  await expect.poll(async () => (await voice(request)).backend).toBe("browser");
  expect((await voice(request)).service).toBeUndefined();
});

test("speech service settings: invalid URL, failed save and conflict preserve deliberate selection", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/settings/voice");
  await page.getByRole("radio", { name: "Your speech service", exact: true }).click();
  const url = page.getByRole("textbox", { name: "Transcription URL", exact: true });
  const save = page.getByRole("button", { name: "Save and use service", exact: true });
  await url.fill("https://speech.example.test/transcriptions?secret=invalid");
  await save.click();
  await walk.state("service-settings-invalid-url", { visible: [page.getByText("Enter a complete http:// or https:// transcription URL.", { exact: true })], hidden: [] });
  expect((await voice(request)).backend).toBe("host");
  await url.fill(endpoint);
  await page.getByText("Key or model (optional)", { exact: true }).click();
  const key = page.getByLabel("API key", { exact: true });
  await key.fill("fictional-private-draft");
  let refuse = true;
  await page.route("**/api/voice", route => route.request().method() === "POST" && refuse
    ? route.fulfill({ status: 503, json: { error: "Fixture unavailable" } }) : route.continue());
  await save.click();
  await walk.state("service-settings-save-failure", { visible: [page.getByRole("alert").filter({ hasText: "Could not save voice settings." }), page.getByRole("button", { name: "Retry", exact: true })], hidden: [] });
  await expect(key).toHaveValue("fictional-private-draft");
  await expect(url).toHaveValue(endpoint);
  expect((await voice(request)).backend).toBe("host");
  const current = await voice(request);
  expect((await request.post("/api/voice", { data: { backend: "browser", selection: current.selection } })).ok()).toBe(true);
  refuse = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await walk.state("service-settings-conflict", { visible: [page.getByRole("button", { name: "Reload settings", exact: true })], hidden: [page.getByRole("button", { name: "Retry", exact: true })] });
  await page.getByRole("button", { name: "Reload settings", exact: true }).click();
  await expect(page.getByRole("radio", { name: "Browser recognition", exact: true })).toBeChecked();
  await page.getByRole("radio", { name: "Your speech service", exact: true }).click();
  await page.getByText("Key or model (optional)", { exact: true }).click();
  await expect(key).toHaveValue("");
});

test("speech service: after-stop transcription, cancellation and post-send disappearance", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  await configure(request);
  const walk = walkthrough(page, info);
  const v = controls(page);
  await walk.open(project.path);
  await v.field.fill("Please");
  let uploads = 0;
  let release = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/voice/service", async route => { uploads += 1; await gate; await route.continue(); });
  try {
    await walk.state("service-01-idle", { visible: [v.mic, page.getByRole("link", { name: /Voice settings.*speech\.example\.test|speech\.example\.test.*Voice settings/ })], hidden: [v.stop] });
    await v.mic.click();
    await walk.state("service-02-listening", { visible: [v.listening, v.cancel, v.stop], hidden: [v.mic] });
    await expect(v.field).toHaveValue("Please");
    expect(uploads).toBe(0);
    await v.stop.click();
    await walk.state("service-03-transcribing", { visible: [page.getByText("Transcribing…", { exact: true }), v.cancel], hidden: [v.main.locator(".composer-wave"), v.main.locator(".composer-timer")] });
    release();
    await expect(v.field).toHaveValue("Please check the build");
    await walk.state("service-04-landed", { visible: [v.mic], hidden: [v.stop, v.cancel, page.getByText("Transcribing…", { exact: true })] });
    expect(uploads).toBe(1);
    await v.mic.click();
    await expect(v.listening).toBeVisible();
    await v.cancel.click();
    await expect(v.field).toHaveValue("Please check the build");
    expect(uploads).toBe(1);
    await v.field.fill("Send");
    await v.mic.click();
    await expect(v.listening).toBeVisible();
    await v.send.click();
    await expect(v.field).toHaveValue("");
    await walk.state("service-05-sent-clears-recording", { visible: [v.mic], hidden: [v.stop, v.cancel, v.main.locator(".composer-wave")] });
    expect(uploads).toBe(2);
  } finally { release(); await page.unrouteAll({ behavior: "wait" }); }
});

for (const failure of ["service_auth", "service_unavailable", "service_response", "service_empty"]) {
  test(`speech service: ${failure} keeps typed draft and does not retry`, async ({ page, request }, info) => {
    const project = await fixtureProject(request);
    await configure(request, failure);
    const walk = walkthrough(page, info);
    const v = controls(page);
    let uploads = 0;
    page.on("request", request => { if (new URL(request.url()).pathname === "/api/voice/service") uploads += 1; });
    await walk.open(project.path);
    await v.field.fill("Keep this draft");
    await v.mic.click();
    await expect(v.listening).toBeVisible();
    await v.send.click();
    await expect(v.mic).toBeVisible();
    await expect(v.field).toHaveValue("Keep this draft");
    await walk.state(`service-error-${failure}`, { visible: [v.main.getByRole("alert")], hidden: [v.stop, v.cancel] });
    expect(uploads).toBe(1);
    await v.field.fill("Typing still works");
  });
}

test("speech service: connection loss is terminal and another conversation receives no words", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  // This journey needs an editable destination; fixtureTask deliberately chooses an archived task.
  const projectData = await (await request.get(`/api/project/${project.name}`)).json();
  const task = projectData.tasks.find((row: { state: string }) => row.state === "running");
  expect(task).toBeTruthy();
  await configure(request);
  const walk = walkthrough(page, info);
  const v = controls(page);
  let uploads = 0;
  await page.route("**/api/voice/service", async route => { uploads += 1; await route.abort("internetdisconnected"); });
  await walk.open(project.path);
  await v.field.fill("Original project draft");
  await v.mic.click();
  await expect(v.listening).toBeVisible();
  await v.stop.click();
  await expect(v.main.getByRole("alert")).toBeVisible();
  await expect(v.field).toHaveValue("Original project draft");
  await expect(v.field).not.toHaveAttribute("readonly", "");
  expect(uploads).toBe(1);
  await page.unrouteAll({ behavior: "wait" });
  await v.mic.click();
  await expect(v.listening).toBeVisible();
  await page.getByRole("link", { name: /^Voice settings:/ }).click();
  await expect(page.getByRole("radio", { name: "Your speech service", exact: true })).toBeChecked();
  await walk.open(`${project.path}/tasks/${encodeURIComponent(task.slug)}`);
  await walk.state("service-navigation-cancels-unsubmitted-audio", {
    visible: [page.getByRole("main").getByRole("textbox", { name: /Message/, includeHidden: true })],
    hidden: [page.getByRole("button", { name: "Stop voice input", exact: true })],
  });
  await expect(page.getByRole("main").getByRole("textbox", { name: /Message/, includeHidden: true })).not.toHaveValue(/check the build/);
  expect(uploads).toBe(1);
});

test("speech service: pending task Send stays with its source after navigation", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const data = await (await request.get(`/api/project/${project.name}`)).json();
  const task = data.tasks.find((row: { state: string }) => row.state === "running");
  expect(task).toBeTruthy();
  await configure(request);
  const walk = walkthrough(page, info);
  await walk.open(`${project.path}/tasks/${task.slug}`);
  const field = page.getByRole("main").getByRole("textbox", { name: /Message/, includeHidden: true });
  await field.fill("Task only");
  let release = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  let uploads = 0;
  const messages: { slug?: string; text: string }[] = [];
  page.on("request", request => {
    if (new URL(request.url()).pathname === "/api/l2/message") messages.push(request.postDataJSON());
  });
  await page.route("**/api/voice/service", async route => { uploads += 1; await gate; await route.continue(); });
  try {
    await page.getByRole("button", { name: "Start voice input", exact: true }).click();
    await expect(page.locator('.composer[data-phase="listening"]')).toBeVisible();
    await page.getByRole("main").getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(() => uploads).toBe(1);
    await walk.state("service-task-pending-send", { visible: [page.getByText("Transcribing to send…", { exact: true })], hidden: [page.locator(".composer-wave"), page.locator(".composer-timer")] });
    const nav = page.getByRole("navigation", { name: info.project.name === "phone" ? "Primary" : "Rail", exact: true });
    await nav.locator(`a[href="${project.path}"]`).click();
    const destination = controls(page).field;
    await destination.fill("Independent project draft");
    release();
    await expect.poll(() => messages.length).toBe(1);
    expect(messages[0]).toMatchObject({ slug: task.slug, text: "Task only check the build" });
    await expect(destination).toHaveValue("Independent project draft");
    await walk.state("service-task-send-preserves-destination", { visible: [destination, controls(page).mic], hidden: [page.getByText("Transcribing to send…", { exact: true })] });
  } finally { release(); await page.unrouteAll({ behavior: "wait" }); }
});

test("speech service: denied microphone leaves typing usable without upload @chromium", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  await configure(request);
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => { throw new DOMException("Fixture denial", "NotAllowedError"); };
  });
  let uploads = 0;
  page.on("request", request => { if (new URL(request.url()).pathname === "/api/voice/service") uploads += 1; });
  const walk = walkthrough(page, info);
  await walk.open(project.path);
  const v = controls(page);
  await v.field.fill("Keep typed words");
  await v.mic.click();
  await walk.state("service-microphone-denied", { visible: [v.main.getByRole("alert").filter({ hasText: "Microphone blocked" }), v.mic], hidden: [v.stop, v.cancel] });
  await expect(v.field).toHaveValue("Keep typed words");
  await expect(v.field).toBeEditable();
  expect(uploads).toBe(0);
});
