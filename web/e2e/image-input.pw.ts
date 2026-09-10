import { expect, type Page, type TestInfo } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "image-input-service.py" });
test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "ignoreErrors" }); });

type Scope = "project" | "task";
const path = (scope: Scope, project = "alpha") => `/projects/${project}${scope === "task" ? "/tasks/image-task" : ""}`;
const endpoint = (scope: Scope) => scope === "task" ? "/api/l2/message" : "/api/chat";
const caption = "The timer covers the result count.";
const FAKE_MIC = `
  const context = new AudioContext();
  const oscillator = context.createOscillator(); oscillator.frequency.value = 220; oscillator.start();
  Object.defineProperty(navigator.mediaDevices, "getUserMedia", { configurable: true, value: async () => {
    await context.resume(); const destination = context.createMediaStreamDestination(); oscillator.connect(destination); return destination.stream;
  }});
`;

function controls(page: Page, scope: Scope) {
  const composer = page.locator(".composer");
  return {
    composer,
    field: page.getByRole("textbox", { name: scope === "project" ? "Message L3 about alpha" : "Message the L2" }),
    picker: page.getByLabel("Choose images"),
    add: page.getByRole("button", { name: "Add images", exact: true }),
    strip: page.getByLabel("Selected images", { exact: true }),
    send: composer.getByRole("button", { name: /^(Send|Queue)$/ }),
    preview: page.getByRole("button", { name: "Open image timer.png", exact: true }),
    remove: page.getByRole("button", { name: "Remove image timer.png", exact: true }),
  };
}

async function open(page: Page, scope: Scope, info: TestInfo) {
  const capability = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/images/alpha");
  await walkthrough(page, info).open(path(scope));
  expect((await capability).ok()).toBe(true);
}

/** Fictional screenshot rendered locally, with no external image or provider calls. */
async function screenshotFile(page: Page, name = "timer.png") {
  const data = await page.evaluate(() => {
    const canvas = document.createElement("canvas"); canvas.width = 640; canvas.height = 360;
    const context = canvas.getContext("2d")!;
    context.fillStyle = "#f8fafc"; context.fillRect(0, 0, 640, 360);
    context.fillStyle = "#172554"; context.font = "bold 28px sans-serif"; context.fillText("Atlas search", 28, 50);
    context.fillStyle = "#e2e8f0"; context.fillRect(28, 74, 584, 50);
    context.fillStyle = "#475569"; context.font = "18px sans-serif"; context.fillText("Index migration", 44, 106);
    context.fillText("12 results", 28, 166);
    context.fillStyle = "#4f46e5"; context.fillRect(58, 140, 80, 34);
    context.fillStyle = "#fff"; context.fillText("0:12", 76, 164);
    context.strokeStyle = "#dc2626"; context.lineWidth = 3; context.strokeRect(20, 134, 130, 46);
    context.fillStyle = "#cbd5e1"; [206, 246, 286].forEach((y) => context.fillRect(28, y, 420, 16));
    return canvas.toDataURL("image/png").split(",")[1]!;
  });
  return { name, mimeType: "image/png", buffer: Buffer.from(data, "base64") };
}

async function dimensionFile(page: Page, name: string, width: number, height: number) {
  const data = await page.evaluate(({ width, height }) => {
    const canvas = document.createElement("canvas"); canvas.width = width; canvas.height = height;
    canvas.getContext("2d")!.fillRect(0, 0, width, height);
    return canvas.toDataURL("image/png").split(",")[1]!;
  }, { width, height });
  return { name, mimeType: "image/png", buffer: Buffer.from(data, "base64") };
}

for (const scope of ["project", "task"] as const) {
  test(`${scope}: real selection, immutable admission, sent viewer and reload`, async ({ page }, info) => {
    const walk = walkthrough(page, info);
    await open(page, scope, info);
    const v = controls(page, scope);
    const file = await screenshotFile(page);
    await walk.state("01-empty", { visible: [v.add, v.field], hidden: [v.strip] });
    await expect(v.add).toHaveCSS("width", "44px");
    await v.field.fill(caption);
    await walk.state("02-four-previews", {
      action: () => v.picker.setInputFiles([file, ...[2, 3, 4].map((n) => ({ ...file, name: `timer-${n}.png` }))]),
      visible: [v.strip, v.remove], hidden: [],
    });
    await expect(v.strip).toHaveCSS("height", "64px");
    await walk.state("02b-count-limit-keeps-four", { action: () => v.picker.setInputFiles({ ...file, name: "fifth.png" }), visible: [v.strip, page.getByText(/Up to 4 images per message\./)], hidden: [page.getByRole("button", { name: "Remove image fifth.png", exact: true })] });
    for (const name of ["timer.png", "timer-2.png", "timer-3.png", "timer-4.png"]) await page.getByRole("button", { name: `Remove image ${name}`, exact: true }).click();
    await walk.state("03-last-removal", { visible: [v.field, v.add], hidden: [v.strip] });
    await expect(v.field).toHaveValue(caption);
    await v.picker.setInputFiles(file);
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route((url) => url.pathname === endpoint(scope), async (route) => {
      await gate;
      const response = await route.fetch();
      await route.fulfill({ response });
    });
    await walk.state("04-sending-images", { action: () => v.send.click(), visible: [page.getByText("Sending images…", { exact: true }), page.getByLabel("Sending images", { exact: true })], hidden: [v.strip] });
    await expect(v.field).toBeDisabled(); await expect(v.add).toBeDisabled();
    release();
    await walk.state("05-saved-image", { visible: [v.preview], hidden: [v.strip, page.getByText("Sending images…", { exact: true })] });
    await expect(v.field).toBeEnabled(); await expect(v.field).toHaveValue("");
    await walk.state("06-view-image", { action: () => v.preview.click(), visible: [page.getByRole("dialog", { name: "Image timer.png" })], hidden: [] });
    await walk.state("07-zoom-image", { action: () => page.getByRole("button", { name: "Zoom image", exact: true }).click(), visible: [page.getByRole("button", { name: "Fit image", exact: true })], hidden: [page.getByRole("button", { name: "Zoom image", exact: true })] });
    await page.keyboard.press("Escape");
    await expect(v.preview).toBeFocused();
    await walk.state("08-close-image", { visible: [v.preview], hidden: [page.getByRole("dialog")] });
    await page.reload();
    await walk.state("09-reload-saved-image", { visible: [v.preview], hidden: [v.strip] });
  });

  test(`${scope}: refusal and lost response retry preserve one durable message`, async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    await open(page, scope, info);
    const v = controls(page, scope);
    await v.picker.setInputFiles(await screenshotFile(page)); await v.field.fill(caption);
    let mode = "refused";
    const submissions: { request_id: string; images: unknown[]; text: string }[] = [];
    await page.route((url) => url.pathname === endpoint(scope), async (route) => {
      submissions.push(route.request().postDataJSON());
      if (mode === "refused") return route.fulfill({ status: 422, json: { error: "Choose a smaller image." } });
      const response = await route.fetch();
      if (mode === "lost") return route.abort("connectionreset");
      return route.fulfill({ response });
    });
    await walk.state("01-refused-draft", { action: () => v.send.click(), visible: [v.strip, page.getByText(/Not sent\. Choose a smaller image\./)], hidden: [page.getByLabel("Sending images", { exact: true })] });
    await expect(v.field).toHaveValue(caption);
    mode = "lost";
    await walk.state("02-unconfirmed-send", { action: () => v.composer.getByRole("button", { name: "Retry", exact: true }).click(), visible: [page.getByText("Could not confirm send.", { exact: false })], hidden: [v.strip] });
    await expect(v.field).toBeDisabled();
    mode = "normal";
    await walk.state("03-safe-retry-accepted", { action: () => v.composer.getByRole("button", { name: "Retry", exact: true }).click(), visible: [v.preview], hidden: [page.getByText("Could not confirm send.", { exact: false }), v.strip] });
    expect(submissions[1]).toEqual(submissions[2]);
    expect(submissions[0]?.request_id).not.toBe(submissions[1]?.request_id);
    await page.reload();
    await expect(v.preview).toHaveCount(1);
    const view = await (await request.get(scope === "task" ? "/api/task/alpha/image-task" : "/api/chat/alpha")).json();
    const rows = scope === "task" ? view.messages : [...view.history, ...(view.queued ?? [])];
    expect(rows.filter((row: { text: string }) => row.text === caption)).toHaveLength(1);
  });

  test(`${scope}: input rejection, private-read loading, denied and missing recovery`, async ({ page }, info) => {
    const walk = walkthrough(page, info);
    await open(page, scope, info);
    const v = controls(page, scope);
    const file = await screenshotFile(page);
    await v.field.fill(caption); await v.picker.setInputFiles(file);
    await walk.state("01-unsupported-input", { action: () => v.picker.setInputFiles({ name: "vector.svg", mimeType: "image/svg+xml", buffer: Buffer.from("<svg/>") }), visible: [v.strip, page.getByText(/Choose PNG, JPEG or static WebP\./)], hidden: [] });
    await walk.state("02-oversized-input", { action: () => v.picker.setInputFiles({ name: "large.png", mimeType: "image/png", buffer: Buffer.alloc((10 << 20) + 1) }), visible: [v.strip, page.getByText(/large.png exceeds 10 MiB/)], hidden: [] });
    await walk.state("02b-unreadable-image-keeps-valid-selection", { action: () => v.picker.setInputFiles({ name: "corrupt.png", mimeType: "image/png", buffer: Buffer.from("\x89PNG\r\n\x1a\ncorrupt", "binary") }), visible: [v.strip, page.getByText(/corrupt.png could not be read\./)], hidden: [page.getByRole("button", { name: "Remove image corrupt.png", exact: true })] });
    const wide = await dimensionFile(page, "wide.png", 8193, 1);
    await walk.state("02c-dimension-limit-keeps-valid-selection", { action: () => v.picker.setInputFiles(wide), visible: [v.strip, page.getByText(/wide.png exceeds 25 megapixels or 8192 pixels per side\./)], hidden: [page.getByRole("button", { name: "Remove image wide.png", exact: true })] });
    const pixels = await dimensionFile(page, "pixels.png", 5001, 5000);
    await walk.state("02d-megapixel-limit-keeps-valid-selection", { action: () => v.picker.setInputFiles(pixels), visible: [v.strip, page.getByText(/pixels.png exceeds 25 megapixels or 8192 pixels per side\./)], hidden: [page.getByRole("button", { name: "Remove image pixels.png", exact: true })] });
    await expect(v.field).toHaveValue(caption); await expect(v.strip.locator("img")).toHaveCount(1);
    let mode = "loading";
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route((url) => /^\/api\/images\/alpha\/[^/]+$/.test(url.pathname), async (route) => {
      if (mode === "loading") await gate;
      if (mode === "denied") return route.fulfill({ status: 403, json: { error: "denied" } });
      if (mode === "missing") return route.fulfill({ status: 404, json: { error: "missing" } });
      return route.continue();
    });
    await walk.state("03-private-image-loading", { action: () => v.send.click(), visible: [page.getByText("Loading image…", { exact: true })], hidden: [v.strip] });
    mode = "denied"; release();
    await walk.state("04-private-image-denied", { visible: [page.getByText("Image access denied.", { exact: true })], hidden: [page.getByText("Loading image…", { exact: true })] });
    mode = "missing";
    await walk.state("05-private-image-missing", { action: () => page.getByRole("button", { name: "Retry image timer.png", exact: true }).click(), visible: [page.getByText("Image unavailable.", { exact: true })], hidden: [page.getByText("Image access denied.", { exact: true })] });
    mode = "normal";
    await walk.state("06-private-image-recovered", { action: () => page.getByRole("button", { name: "Retry image timer.png", exact: true }).click(), visible: [v.preview], hidden: [page.getByText("Image unavailable.", { exact: true })] });
    await expect(page.locator(".bubble").filter({ hasText: caption })).toBeVisible();
  });

  test(`${scope}: image selection survives voice cancel, transcription and send`, async ({ page }, info) => {
    await page.addInitScript(FAKE_MIC);
    const walk = walkthrough(page, info);
    await open(page, scope, info);
    const v = controls(page, scope);
    await v.picker.setInputFiles(await screenshotFile(page)); await v.field.fill(caption);
    const start = page.getByRole("button", { name: "Start voice input", exact: true });
    const stop = page.getByRole("button", { name: "Stop voice input", exact: true });
    await walk.state("01-listening-with-image", { action: () => start.click(), visible: [stop, v.strip], hidden: [v.add] });
    await v.field.fill(`${caption} Keep this edit.`);
    await walk.state("02-voice-cancel-keeps-image", { action: () => page.getByRole("button", { name: "Cancel voice input", exact: true }).click(), visible: [v.strip, v.add, start], hidden: [stop] });
    await expect(v.field).toHaveValue(`${caption} Keep this edit.`);
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    let transcript = "Please fix the overlap.";
    await page.route("**/api/transcribe", async (route) => { await gate; return route.fulfill({ json: { text: transcript } }); });
    await start.click(); await expect(stop).toBeVisible();
    // Chromium needs an actual recorded interval before it can encode audio data.
    await page.waitForTimeout(700);
    await walk.state("03-transcribing-with-image", { action: () => stop.click(), visible: [v.strip, page.getByText("Transcribing…", { exact: true })], hidden: [v.add, stop] });
    release();
    await expect(v.field).toHaveValue(`${caption} Keep this edit. Please fix the overlap.`);
    await walk.state("04-transcript-in-draft-only", { visible: [v.strip, v.add, start], hidden: [page.getByText("Transcribing…", { exact: true }), page.getByText("Please fix the overlap.", { exact: true })] });
    transcript = "  ";
    await start.click(); await expect(stop).toBeVisible(); await page.waitForTimeout(700); await v.send.click();
    await expect(start).toBeEnabled(); await expect(v.strip).toBeVisible();
    await expect(v.preview).toHaveCount(0);
    transcript = "And keep search working.";
    await start.click(); await expect(stop).toBeVisible(); await page.waitForTimeout(700); await v.send.click();
    await walk.state("05-voice-send-clears-image-and-draft", { visible: [v.preview, start], hidden: [v.strip, page.getByText("Transcribing…", { exact: true })] });
    await expect(v.field).toHaveValue("");
    await page.evaluate(() => { Object.defineProperty(navigator.mediaDevices, "getUserMedia", { configurable: true, value: async () => { throw new DOMException("denied", "NotAllowedError"); } }); });
    await walk.state("06-microphone-denied", { action: () => start.click(), visible: [page.getByText("Microphone blocked in the browser. Typing works.", { exact: true }), v.add], hidden: [v.strip] });
    await expect(start).toBeDisabled(); await expect(v.field).toBeEnabled();
  });

  test(`${scope}: unavailable image input keeps text and ordinary sends usable`, async ({ page, request }, info) => {
    await request.post("/fixture/image-mode", { data: { mode: "unavailable" } });
    const walk = walkthrough(page, info);
    await open(page, scope, info);
    const v = controls(page, scope);
    await v.field.fill("Plain text still works.");
    await walk.state("01-image-capability-unavailable", { action: () => v.add.click(), visible: [page.getByText(/Image input unavailable\./), v.field], hidden: [v.strip] });
    await expect(v.send).toBeEnabled();
    await v.send.click();
    await expect(page.locator(".bubble").filter({ hasText: "Plain text still works." })).toBeVisible();
  });
}

test("project: busy queue preserves images, removal cancels one message, and failure Retry reuses bytes", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await open(page, "project", info);
  const v = controls(page, "project");
  const file = await screenshotFile(page);
  await request.post("/fixture/pause/alpha");
  await v.picker.setInputFiles(file); await v.field.fill(caption); await v.send.click();
  await expect.poll(async () => (await (await request.get("/fixture/calls")).json()).calls.length).toBe(1);
  await expect(v.field).toBeEnabled();
  const queued = page.getByRole("list", { name: "Queued messages", exact: true });
  await v.picker.setInputFiles({ ...file, name: "queued.png" }); await v.field.fill("Inspect this next.");
  await walk.state("01-busy-queued-image", { action: () => v.send.click(), visible: [queued.getByRole("button", { name: "Open image queued.png", exact: true }), queued.getByRole("button", { name: "Remove", exact: true })], hidden: [v.strip] });
  await page.reload();
  await walk.state("02-queue-survives-reload", { visible: [queued.getByRole("button", { name: "Open image queued.png", exact: true })], hidden: [v.strip] });
  await walk.state("03-queue-removal", { action: () => queued.getByRole("button", { name: "Remove", exact: true }).click(), visible: [v.preview], hidden: [queued] });
  await request.post("/fixture/image-mode", { data: { mode: "fail-turn" } });
  await request.post("/fixture/release/alpha");
  await page.reload();
  const failure = page.locator(".turn-failed").filter({ hasText: "L3 could not answer this turn." });
  await walk.state("04-saved-image-delivery-failure", { visible: [failure, v.preview], hidden: [v.strip] });
  await failure.getByRole("button", { name: "Retry", exact: true }).click();
  await expect.poll(async () => (await (await request.get("/fixture/calls")).json()).calls.length).toBe(2);
  const calls = (await (await request.get("/fixture/calls")).json()).calls;
  expect(calls[0].images).toEqual(calls[1].images);
  expect(calls[0].resume).toEqual(calls[1].resume);
  await page.reload();
  await walk.state("05-saved-image-retry-completed", { visible: [page.getByText(`I can inspect 1 image(s). ${caption}`, { exact: true })], hidden: [v.strip] });
});

test("late admission and clipboard selection stay with the original project", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await open(page, "project", info);
  const v = controls(page, "project");
  const file = await screenshotFile(page);
  await v.field.focus();
  await page.evaluate((data) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([Uint8Array.from(atob(data), (value) => value.charCodeAt(0))], "timer.png", { type: "image/png" }));
    document.querySelector("textarea")!.dispatchEvent(new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true, cancelable: true }));
  }, file.buffer.toString("base64"));
  await walk.state("01-clipboard-image-selected", { visible: [v.strip, v.remove], hidden: [] });
  await v.field.fill(caption);
  let accepted: () => void = () => undefined;
  const saved = new Promise<void>((resolve) => { accepted = resolve; });
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/chat", async (route) => { const response = await route.fetch(); accepted(); await gate; await route.fulfill({ response }).catch(() => undefined); });
  await v.send.click(); await saved;
  // Navigate within the app; the original HTTP admission can finish after beta mounts.
  if (info.project.name === "phone") {
    await page.getByRole("button", { name: "alpha", exact: true }).click();
    await page.getByRole("dialog", { name: "Switch project", exact: true }).getByRole("link", { name: "beta", exact: true }).click();
  } else await page.getByRole("link", { name: "beta", exact: true }).click();
  release();
  const betaField = page.getByRole("textbox", { name: "Message L3 about beta", exact: true });
  await betaField.fill("Keep this beta draft.");
  await walk.state("02-late-response-stays-in-alpha", { visible: [betaField], hidden: [v.strip, v.preview] });
  await expect(betaField).toHaveValue("Keep this beta draft.");
  const beta = await (await request.get("/api/chat/beta")).json();
  expect([...beta.history, ...(beta.queued ?? [])].some((row: { images?: unknown[] }) => row.images?.length)).toBe(false);
  await page.goto(path("project"));
  await walk.state("03-return-to-original-saved-image", { visible: [v.preview], hidden: [v.strip] });
});

test("task: image-only checkpoint delivery and archived viewing retain the original image", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await open(page, "task", info);
  const v = controls(page, "task");
  await v.picker.setInputFiles(await screenshotFile(page)); await expect(v.field).toHaveValue(""); await v.send.click();
  await expect(v.preview).toBeVisible();
  const delivered = await request.post("/fixture/task-deliver");
  expect(delivered.ok()).toBe(true);
  expect((await delivered.json()).images).toHaveLength(1);
  await page.reload();
  await walk.state("01-image-only-delivered-at-checkpoint", { visible: [v.preview, page.getByText("I can inspect 1 attached image(s).", { exact: true })], hidden: [v.strip] });
  expect((await request.post("/fixture/archive-task")).ok()).toBe(true);
  await page.reload();
  await walk.state("02-archived-image-conversation", { visible: [v.preview], hidden: [v.composer] });
  await walk.state("03-archived-image-viewer", { action: () => v.preview.click(), visible: [page.getByRole("dialog", { name: "Image timer.png" })], hidden: [v.composer] });
  await page.getByRole("button", { name: "Close image", exact: true }).click();
  await expect(v.preview).toBeFocused();
});

test("an earlier text stream cannot replace or retire a pending image admission", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await open(page, "project", info);
  const v = controls(page, "project");
  await request.post("/fixture/pause/alpha");
  await v.field.fill("Answer this first text turn."); await v.send.click();
  await expect.poll(async () => (await (await request.get("/fixture/calls")).json()).calls.length).toBe(1);
  await v.picker.setInputFiles(await screenshotFile(page)); await expect(v.remove).toBeVisible(); await v.field.fill(caption);
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/chat", async (route) => { await gate; const response = await route.fetch(); await route.fulfill({ response }); });
  await walk.state("01-image-pending-behind-stream", { action: () => v.send.click(), visible: [page.getByText("Sending images…", { exact: true }), page.getByLabel("Sending images", { exact: true })], hidden: [v.strip] });
  await request.post("/fixture/release/alpha");
  const reply = "I can inspect 0 image(s). Answer this first text turn.";
  await expect.poll(async () => (await (await request.get("/api/chat/alpha")).json()).history.some((row: { text: string }) => row.text === reply)).toBe(true);
  await expect(page.getByText(reply, { exact: true })).toBeVisible();
  const pending = page.locator(".turn[data-local]");
  await walk.state("02-original-stream-finished-image-stays-pending", { visible: [pending, pending.getByLabel("Sending images", { exact: true }), page.getByText("Sending images…", { exact: true })], hidden: [pending.locator(".reply")] });
  await expect(pending).toContainText(caption);
  await expect(pending.locator(".msg-row[data-pending]")).toHaveCount(1);
  await expect(v.field).toBeDisabled();
  release();
  await walk.state("03-image-admission-finishes-independently", { visible: [v.preview, page.getByText(reply, { exact: true })], hidden: [page.getByText("Sending images…", { exact: true }), pending] });
});
