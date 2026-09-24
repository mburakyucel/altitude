import { test } from "./fixtures";
import { expect, type Page, type TestInfo } from "@playwright/test";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/*
 * The default voice backend: the browser's own speech recognition (SPEC.md §3.6), walked at 390 and
 * 1440. The recognizer is a page-level fake driven by the test (Playwright's Chromium has no working
 * one); the installation answers "browser" through an overlaid /api/voice. Nothing is uploaded: every
 * POST /api/transcribe and /api/chat is intercepted and counted.
 */

const FAKE_RECOGNIZER = `
  class FixtureRecognition {
    constructor() { this.continuous = false; this.interimResults = true; this.lang = ""; this.onresult = null; this.onerror = null; this.onend = null; this.started = 0; window.fixtureRecognizer = this; }
    start() { this.started += 1; }
    stop() { setTimeout(() => this.onend && this.onend(), 0); }
    abort() { setTimeout(() => this.onend && this.onend(), 0); }
    hear(finals, interim) {
      const results = finals.map((transcript) => ({ isFinal: true, 0: { transcript }, length: 1 }));
      if (interim) results.push({ isFinal: false, 0: { transcript: interim }, length: 1 });
      this.onresult && this.onresult({ results });
    }
    fail(error) { this.onerror && this.onerror({ error }); this.onend && this.onend(); }
  }
  window.SpeechRecognition = FixtureRecognition;
`;

function views(page: Page, info: TestInfo) {
  const main = page.getByRole("main");
  return {
    phone: info.project.name === "phone",
    main,
    field: main.getByRole("textbox", { name: /^Message L3 about /, includeHidden: true }),
    send: main.getByRole("button", { name: "Send", exact: true }),
    mic: main.getByRole("button", { name: "Start voice input", exact: true }),
    stop: main.getByRole("button", { name: "Stop voice input", exact: true }),
    cancel: main.getByRole("button", { name: "Cancel voice input", exact: true }),
    hint: main.locator(".composer-hint"),
    wave: main.locator(".composer-wave"),
    timer: main.getByLabel("Recording time", { exact: true }),
    transcribing: main.getByText("Transcribing…", { exact: true }),
    // Stop shows while the microphone opens; the recognizer exists once the composer is listening.
    listening: main.locator('.composer[data-phase="listening"]'),
    bubble: (text: string) => main.getByRole("region", { name: "Conversation", exact: true }).locator(".bubble", { hasText: text }),
  };
}

async function browserBackend(page: Page) {
  await page.route((url) => url.pathname === "/api/voice", (route) => route.fulfill({ json: { backend: "browser" } }));
}

const hear = (page: Page, finals: string[], interim = "") => page.evaluate(([f, i]) => (window as unknown as { fixtureRecognizer: { hear(f: string[], i: string): void } }).fixtureRecognizer.hear(f, i), [finals, interim] as const);

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
});

for (const supported of [true, false]) {
  test(`browser punctuation ${supported ? "supported" : "unavailable"}: progressive text and Stop preserve native formatting`, async ({ page, request }, info) => {
    const project = await fixtureProject(request);
    const walk = walkthrough(page, info);
    const v = views(page, info);
    await browserBackend(page);
    await page.addInitScript(FAKE_RECOGNIZER + (supported ? "FixtureRecognition.prototype.unspokenPunctuation = false;" : ""));
    await walk.open(project.path);
    await v.field.fill("Typed prefix:");
    await v.mic.click();
    await expect(v.listening).toBeVisible();
    expect(await page.evaluate("window.fixtureRecognizer.unspokenPunctuation")).toBe(supported ? true : undefined);
    const fragments = supported ? ["Hello, world!", "Is this ready?"] : ["a fragment", "of one sentence"];
    await walk.state("punctuation-listening", {
      action: () => hear(page, [fragments[0]!], fragments[1]!),
      visible: [v.field, v.stop, v.cancel],
      hidden: [v.mic, v.transcribing],
    });
    await expect(v.field).toHaveValue(`Typed prefix: ${fragments.join(" ")}`);
    await hear(page, fragments);
    await walk.state("punctuation-stopped-editable", {
      action: () => v.stop.click(),
      visible: [v.field, v.mic, v.send],
      hidden: [v.stop, v.cancel, v.transcribing],
    });
    await expect(v.field).toBeEditable();
    await expect(v.field).toHaveValue(`Typed prefix: ${fragments.join(" ")}`);
  });
}

test("browser recognition: words appear while listening, Stop lands them, Send at once, Cancel discards, a recognizer error keeps the words", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  await browserBackend(page);
  await page.addInitScript(FAKE_RECOGNIZER);
  const uploads: string[] = [];
  const posts: string[] = [];
  await page.route((url) => url.pathname === "/api/transcribe", (route) => {
    uploads.push(route.request().method());
    return route.fulfill({ status: 409, json: { error: "Unexpected upload" } });
  });
  let releaseSend: () => void = () => {};
  const sendGate = new Promise<void>((resolve) => (releaseSend = resolve));
  await page.route((url) => url.pathname === "/api/chat", async (route) => {
    posts.push(route.request().postData() ?? "");
    await sendGate;
    return route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({ t: "Heard you." })}\n${JSON.stringify({ done: { turn_id: "ui-recognition" } })}\n`,
    });
  });

  await walk.open(project.path);
  await v.field.fill("Fix the timer");
  await walk.state("01-listening-no-words-yet", {
    action: () => v.mic.click(),
    visible: [v.listening, v.stop, v.cancel, v.send, v.wave, v.timer, v.hint, v.field],
    hidden: [v.mic, v.transcribing],
  });
  await expect(v.hint).toHaveText("Listening… Stop to add text, or Send.");
  await expect(v.field).toHaveValue("Fix the timer");
  await expect(v.field).not.toBeEditable();
  expect(await page.evaluate("window.fixtureRecognizer.continuous")).toBe(true);
  expect(await page.evaluate("window.fixtureRecognizer.started")).toBe(1);
  const row = await v.main.locator(".composer-row").boundingBox();
  expect(row).not.toBeNull();
  // The row sits under the field on both widths; desktop keeps the recording cluster bounded at the right and the phone lets the waveform fill the row.
  expect(row!.y).toBeGreaterThanOrEqual((await v.field.boundingBox())!.y + (await v.field.boundingBox())!.height - 1);
  if (v.phone) expect((await v.wave.boundingBox())!.width).toBeGreaterThan(row!.width / 3);
  else await expect(v.wave).toHaveCSS("width", "168px");
  const canvas = await v.wave.evaluate((node) => ({ dpr: window.devicePixelRatio, width: (node as HTMLCanvasElement).width, css: node.getBoundingClientRect().width }));
  expect(canvas.width).toBe(Math.round(canvas.css * canvas.dpr));
  await walk.state("02-words-appear-while-listening", {
    action: () => hear(page, ["and the tests"], "on both"),
    visible: [v.stop, v.field],
    hidden: [v.transcribing, v.main.getByRole("region", { name: /transcript/i })],
  });
  await expect(v.field).toHaveValue("Fix the timer and the tests on both");
  const dictation = Array.from({ length: 30 }, (_, line) => `line ${line + 1} of a long dictation that keeps going past the field's height`);
  await walk.state("02b-latest-words-stay-in-view", {
    action: () => hear(page, ["and the tests", ...dictation], "on both sizes"),
    visible: [v.stop, v.field],
    hidden: [v.transcribing],
  });
  await expect(v.field).toHaveValue(`Fix the timer and the tests ${dictation.join(" ")} on both sizes`);
  const scroll = await v.field.evaluate((node) => ({ top: node.scrollTop, client: node.clientHeight, height: node.scrollHeight }));
  expect(scroll.height).toBeGreaterThan(scroll.client);
  expect(scroll.top + scroll.client).toBeGreaterThanOrEqual(scroll.height - 1);
  await hear(page, ["and the tests", "on both sizes"]);
  await expect(v.field).toHaveValue("Fix the timer and the tests on both sizes");
  await walk.state("03-stopped-words-landed-nothing-else-appears", {
    action: () => v.stop.click(),
    visible: [v.mic, v.send, v.field],
    hidden: [v.stop, v.cancel, v.wave, v.timer, v.transcribing, v.main.getByText("and the tests on both sizes", { exact: true }), ...(v.phone ? [v.hint] : [])],
  });
  await expect(v.field).toHaveValue("Fix the timer and the tests on both sizes");
  await expect(v.field).toBeEditable();
  await expect(v.field).toBeFocused();
  expect(await v.field.evaluate((node) => (node as HTMLTextAreaElement).selectionStart)).toBe("Fix the timer and the tests on both sizes".length);
  await page.keyboard.type(" please");
  await expect(v.field).toHaveValue("Fix the timer and the tests on both sizes please");
  expect(uploads).toEqual([]);
  expect(posts).toEqual([]);

  await v.field.fill("Keep this");
  await v.mic.click();
  await expect(v.listening).toBeVisible();
  await hear(page, ["forget it"], "and this");
  await expect(v.field).toHaveValue("Keep this forget it and this");
  await walk.state("04-cancel-discards-the-words", {
    action: () => page.keyboard.press("Escape"),
    visible: [v.mic, v.field],
    hidden: [v.stop, v.cancel, v.wave, v.main.getByText("forget it", { exact: true })],
  });
  await expect(v.field).toHaveValue("Keep this");
  await expect(v.field).toBeEditable();

  await v.mic.click();
  await expect(v.listening).toBeVisible();
  await hear(page, ["send this now"]);
  await expect(v.field).toHaveValue("Keep this send this now");
  await walk.state("05-send-at-once-pending-bubble", {
    action: () => v.send.click(),
    visible: [v.bubble("Keep this send this now"), v.mic],
    hidden: [v.stop, v.cancel, v.transcribing],
  });
  await expect(v.field).toHaveValue("");
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0]!)).toMatchObject({ text: "Keep this send this now" });
  releaseSend();
  await expect(v.main.getByText("Heard you.", { exact: true })).toBeVisible();
  expect(uploads).toEqual([]);

  await v.field.fill("Still here");
  await v.mic.click();
  await expect(v.listening).toBeVisible();
  await hear(page, [], "half a");
  await expect(v.field).toHaveValue("Still here half a");
  const failure = v.main.getByRole("alert").filter({ hasText: "Could not transcribe. Typing works." });
  await walk.state("06-recognizer-failed-words-kept-typing-works", {
    action: () => page.evaluate("window.fixtureRecognizer.fail('network')"),
    visible: [failure, v.mic, v.field],
    hidden: [v.stop, v.cancel, v.wave, v.transcribing],
  });
  await expect(v.field).toHaveValue("Still here half a");
  await expect(v.field).toBeEditable();
  await expect(v.mic).toBeEnabled();
  expect(uploads).toEqual([]);
  expect(posts).toHaveLength(1);
});

test("browser recognition: denied by the recognizer, and a browser without one says typing works", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  await browserBackend(page);
  await page.addInitScript(FAKE_RECOGNIZER);
  await walk.open(project.path);
  await v.field.fill("Typing still works");
  await v.mic.click();
  await expect(v.listening).toBeVisible();
  await walk.state("01-denied-by-the-recognizer", {
    action: () => page.evaluate("window.fixtureRecognizer.fail('not-allowed')"),
    visible: [v.main.getByText("Microphone blocked in the browser. Typing works.", { exact: true }), v.mic, v.field],
    hidden: [v.stop, v.cancel, v.wave],
  });
  await expect(v.mic).toBeDisabled();
  await expect(v.field).toHaveValue("Typing still works");
  await expect(v.field).toBeEditable();
  await expect(v.send).toBeEnabled();

  // A browser without speech recognition (Firefox today) shows the hint instead of the microphone.
  const plain = await page.context().newPage();
  await browserBackend(plain);
  await plain.addInitScript("delete window.SpeechRecognition; delete window.webkitSpeechRecognition;");
  const w = views(plain, info);
  const plainWalk = walkthrough(plain, info);
  await plainWalk.open(project.path);
  await plainWalk.state("02-no-speech-recognition-typing-works", {
    visible: [w.field, w.send, w.main.getByText("This browser has no speech recognition. Typing works.", { exact: true })],
    hidden: [w.mic, w.stop],
  });
  await w.field.fill("Typed instead");
  await expect(w.send).toBeEnabled();
  await plain.close();
});
