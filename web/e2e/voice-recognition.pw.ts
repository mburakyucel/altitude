import { test } from "./fixtures";
import { expect, type Page, type TestInfo } from "@playwright/test";
import { fixtureProject, fixtureTask } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/*
 * The default voice backend: the browser's own speech recognition (SPEC.md §3.6), walked at 390 and
 * 1440. The recognizer is a page-level fake driven by the test (Playwright's Chromium has no working
 * one); the installation answers "browser" through an overlaid /api/voice. Nothing is uploaded: every
 * POST /api/transcribe and /api/chat is intercepted and counted.
 */

const FAKE_RECOGNIZER = `
  class FixtureRecognition {
    constructor() { this.continuous = false; this.interimResults = true; this.lang = ""; this.onresult = null; this.onerror = null; this.onend = null; this.started = 0; this.ended = false; window.fixtureRecognizer = this; (window.fixtureRecognizers ??= []).push(this); }
    start() { this.started += 1; }
    stop() { setTimeout(() => this.end(), 0); }
    // window.fixtureHoldAbort: Cancel's abort ends only when the test calls end(), as a recognizer still shutting down.
    abort() { if (!window.fixtureHoldAbort) setTimeout(() => this.end(), 0); }
    end() { if (this.ended) return; this.ended = true; this.onend && this.onend(); }
    hear(finals, interim) {
      const results = finals.map((transcript) => ({ isFinal: true, 0: { transcript }, length: 1 }));
      if (interim) results.push({ isFinal: false, 0: { transcript: interim }, length: 1 });
      this.onresult && this.onresult({ results });
    }
    fail(error) { this.onerror && this.onerror({ error }); this.end(); }
  }
  window.SpeechRecognition = FixtureRecognition;
`;

function views(page: Page, info: TestInfo) {
  const main = page.getByRole("main");
  return {
    phone: info.project.name === "phone",
    main,
    field: main.getByRole("textbox", { name: /^Message (?:L3 about |the L2$)/, includeHidden: true }),
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
  await page.route((url) => url.pathname === "/api/voice", (route) => route.fulfill({ json: { backend: "browser", url: "", model: "", key_set: false, selection: "fixture-browser" } }));
}

const PUNCTUATED_STOP = "So I think we should merge the PR today. Then look at the voice settings tomorrow. What do you think?";
const PUNCTUATED_LISTENING = "Can you check why the CI job failed on the main branch? it looks like";

const hear = (page: Page, finals: string[], interim = "") => page.evaluate(([f, i]) => (window as unknown as { fixtureRecognizer: { hear(f: string[], i: string): void } }).fixtureRecognizer.hear(f, i), [finals, interim] as const);

test("voice diagnostics: opt-in report distinguishes suspended restart and excludes conversation content", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  await browserBackend(page);
  await page.addInitScript(FAKE_RECOGNIZER);
  await page.addInitScript(() => {
    const Native = window.AudioContext;
    let graphs = 0;
    window.AudioContext = class extends Native {
      constructor() { super(); if (++graphs === 2) void this.suspend(); }
    };
    Object.defineProperty(navigator, "clipboard", { value: { writeText: () => Promise.reject(new Error("fixture denial")) } });
  });
  const settings = async () => {
    await page.getByRole("button", { name: "More actions", exact: true }).click();
    await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
    await page.getByRole("link", { name: "Voice input Browser recognition", exact: true }).click();
    await page.getByText("Voice troubleshooting", { exact: true }).click();
    await page.getByText("Collect microphone states, errors, timing and browser version", { exact: false }).scrollIntoViewIfNeeded();
    await expect(page.getByText("Collect microphone states, errors, timing and browser version", { exact: false })).toBeInViewport();
  };
  const start = page.getByRole("button", { name: "Start diagnostics", exact: true });
  const report = page.getByRole("textbox", { name: "Voice diagnostic report", exact: true });
  await walk.open(project.path);
  await v.field.fill("Private typed fixture draft");
  await settings();
  await walk.state("diagnostics-01-off", { visible: [start], hidden: [report] });
  await walk.state("diagnostics-02-collecting", {
    action: () => start.click(),
    visible: [page.getByRole("button", { name: "Stop diagnostics", exact: true }), page.getByText("Collecting on this device. Return here after reproducing.", { exact: true })], hidden: [report],
  });
  await page.goBack();
  await page.goBack();
  await expect(v.field).toHaveValue("Private typed fixture draft");
  for (let cycle = 0; cycle < 2; cycle++) {
    await v.mic.click();
    await expect(v.listening).toBeVisible();
    await hear(page, [], "Private spoken fixture phrase");
    // Let the real graph draw/sample; the second graph is deliberately suspended, not a native claim.
    await expect.poll(() => page.evaluate(() => document.querySelectorAll(".composer-wave").length)).toBe(1);
    await page.waitForTimeout(1100);
    await v.cancel.click();
    await expect(v.field).toHaveValue("Private typed fixture draft");
  }
  await settings();
  await walk.state("diagnostics-03-report", {
    action: async () => {
      await page.getByRole("button", { name: "View report", exact: true }).click();
      await report.scrollIntoViewIfNeeded();
    },
    visible: [report, page.getByRole("button", { name: "Copy report", exact: true }), start],
    hidden: [page.getByRole("button", { name: "Stop diagnostics", exact: true })],
  });
  const text = await report.inputValue();
  expect(text).not.toContain("Private");
  expect(text).not.toContain(project.path);
  const data = JSON.parse(text);
  expect(data.build).toMatch(/^index-.*\.js$/);
  expect(data.events).toEqual(expect.arrayContaining([
    expect.objectContaining({ event: "waveform.sample", state: "running" }),
    expect.objectContaining({ event: "waveform.sample", state: "suspended", signal: false }),
    expect.objectContaining({ event: "recognizer.cancel" }),
    expect.objectContaining({ event: "microphone.released", state: "ended" }),
  ]));
  await walk.state("diagnostics-04-copy-denied-manual-fallback", {
    action: () => page.getByRole("button", { name: "Copy report", exact: true }).click(),
    visible: [report, page.getByText("Could not copy. Select and copy the report above.", { exact: true })], hidden: [],
  });
  await page.evaluate(() => {
    navigator.clipboard.writeText = async (value) => { (window as unknown as { copiedReport: string }).copiedReport = value; };
  });
  await walk.state("diagnostics-04b-copied", {
    action: () => page.getByRole("button", { name: "Copy report", exact: true }).click(),
    visible: [report, page.getByText("Copied. Paste it into the task conversation.", { exact: true })],
    hidden: [page.getByText("Could not copy. Select and copy the report above.", { exact: true })],
  });
  expect(await page.evaluate(() => (window as unknown as { copiedReport: string }).copiedReport)).toBe(text);
  await walk.state("diagnostics-05-cleared", {
    action: () => page.getByRole("button", { name: "Clear report", exact: true }).click(),
    visible: [start, page.getByText("Cleared.", { exact: true })], hidden: [report],
  });
  await start.click();
  await page.reload();
  await page.getByText("Voice troubleshooting", { exact: true }).click();
  await expect(start).toBeVisible();
  await page.getByRole("button", { name: "View report", exact: true }).click();
  expect(JSON.parse(await report.inputValue()).events).toEqual([]);
});

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
});

// The bundled punctuation model is real here; blocking or holding its asset walks first use and failure.
const MODEL = (url: URL) => /\/assets\/model-[^/]+\.ort$/.test(url.pathname);

test("browser punctuation: the phrase being heard stays as heard, a model still downloading lands the words as heard, then finalized phrases are punctuated", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  await browserBackend(page);
  await page.addInitScript(FAKE_RECOGNIZER);
  // First use: the model is still downloading when Stop is pressed.
  let releaseModel: () => void = () => {};
  const modelGate = new Promise<void>((resolve) => (releaseModel = resolve));
  await page.route(MODEL, async (route) => { await modelGate; await route.continue(); });
  await walk.open(project.path);
  await v.mic.click();
  await expect(v.listening).toBeVisible();
  await walk.state("punctuation-01-interim-as-heard", {
    action: () => hear(page, ["so i think we should merge the pr today"], "then look at"),
    visible: [v.field, v.stop, v.cancel],
    hidden: [v.mic, v.transcribing],
  });
  await expect(v.field).toHaveValue("so i think we should merge the pr today then look at");
  await hear(page, ["so i think we should merge the pr today", "then look at the voice settings tomorrow what do you think"]);
  await walk.state("punctuation-02-stop-waits-for-model", {
    action: () => v.stop.click(),
    visible: [v.field, v.transcribing, v.cancel],
    hidden: [v.stop],
  });
  await expect(v.mic).toBeDisabled();
  await walk.state("punctuation-03-landed-while-loading", {
    visible: [v.field, v.mic, v.send, v.hint],
    hidden: [v.stop, v.cancel, v.transcribing],
  });
  await expect(v.field).toBeEditable();
  await expect(v.field).toHaveValue("so i think we should merge the pr today then look at the voice settings tomorrow what do you think");
  await expect(v.hint).toHaveText("Added without punctuation: still loading. Next time it will be ready.");
  releaseModel();

  // Loaded model: a finalized phrase is punctuated while listening; the next phrase is still as heard.
  await v.field.fill("");
  await v.mic.click();
  await expect(v.listening).toBeVisible();
  await expect(v.hint).not.toContainText("without punctuation");
  await walk.state("punctuation-04-finalized-while-listening", {
    action: () => hear(page, ["can you check why the ci job failed on the main branch"], "it looks like"),
    visible: [v.field, v.stop, v.cancel],
    hidden: [v.mic, v.transcribing],
  });
  await expect(v.field).toHaveValue(PUNCTUATED_LISTENING);
  await walk.state("punctuation-05-cancel-discards", {
    action: () => v.cancel.click(),
    visible: [v.field, v.mic],
    hidden: [v.stop, v.cancel, v.transcribing],
  });
  await expect(v.field).toHaveValue("");

  await v.mic.click();
  await expect(v.listening).toBeVisible();
  await hear(page, ["so i think we should merge the pr today"], "then look at");
  await hear(page, ["so i think we should merge the pr today", "then look at the voice settings tomorrow what do you think"]);
  await walk.state("punctuation-06-landed-punctuated", {
    action: () => v.stop.click(),
    visible: [v.field, v.mic, v.send],
    hidden: [v.stop, v.cancel, v.transcribing],
  });
  await expect(v.field).toBeEditable();
  await expect(v.field).toHaveValue(PUNCTUATED_STOP);
  await expect(v.hint).not.toContainText("without punctuation");
});

test("browser punctuation unavailable: the words land as recognized and the composer says so", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  await browserBackend(page);
  await page.addInitScript(FAKE_RECOGNIZER);
  await page.route(MODEL, (route) => route.abort());
  await walk.open(project.path);
  await v.mic.click();
  await expect(v.listening).toBeVisible();
  await hear(page, ["merge the pr today"]);
  await walk.state("punctuation-07-unavailable-landed", {
    action: () => v.stop.click(),
    visible: [v.field, v.mic, v.hint],
    hidden: [v.stop, v.transcribing],
  });
  await expect(v.field).toHaveValue("merge the pr today");
  await expect(v.hint).toHaveText("Added without punctuation: this browser could not run it.");
  await v.field.fill("merge the pr today, typed");
  await v.mic.click();
  await expect(v.hint).not.toContainText("without punctuation");
  await v.cancel.click();
});

// The composer's capture mechanics, with words exactly as the recognizer heard them: a recognition
// language the punctuation model does not cover keeps the recognizer's text (docs/OPERATIONS.md).
test.describe("recognizer text as heard", () => {
  test.use({ locale: "fr-FR" });

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

  test("browser recognition: repeated Cancel and restart; a cancelled recognizer's late end, words and refusal leave the new capture alone", async ({ page, request }, info) => {
    const project = await fixtureProject(request);
    const walk = walkthrough(page, info);
    const v = views(page, info);
    await browserBackend(page);
    await page.addInitScript(FAKE_RECOGNIZER);
    const posts: string[] = [];
    await page.route((url) => url.pathname === "/api/chat", (route) => {
      posts.push(route.request().postData() ?? "");
      return route.fulfill({
        contentType: "application/x-ndjson",
        body: `${JSON.stringify({ t: "Heard the restart." })}\n${JSON.stringify({ done: { turn_id: "ui-restart" } })}\n`,
      });
    });
    // Each capture's microphone is a steady tone of its own, so a restart must show a moving waveform
    // from a live stream, not the flat one the operator saw.
    await page.addInitScript(`
      const NativeAudioContext = window.AudioContext;
      window.fixtureStreams = [];
      window.fixtureWaveforms = [];
      window.AudioContext = class extends NativeAudioContext {
        constructor(...args) {
          super(...args);
          this.fixtureCloseCalled = false;
          window.fixtureWaveforms.push(this);
        }
        close() {
          this.fixtureCloseCalled = true;
          if (!window.fixtureHoldClose) return super.close();
          return new Promise((resolve, reject) => {
            this.fixtureReleaseClose = () => super.close().then(resolve, reject);
          });
        }
      };
      Object.defineProperty(navigator.mediaDevices, "getUserMedia", { configurable: true, value: async () => {
        const context = new NativeAudioContext();
        const oscillator = context.createOscillator();
        const destination = context.createMediaStreamDestination();
        oscillator.connect(destination);
        oscillator.start();
        await context.resume();
        window.fixtureStreams.push(destination.stream);
        return destination.stream;
      }});
    `);
    // The tallest drawn bar as a share of the waveform's height: the silent minimum is a few pixels.
    const loudest = () => page.evaluate(() => {
      const canvas = document.querySelector<HTMLCanvasElement>(".composer-wave");
      const data = canvas?.getContext("2d")?.getImageData(0, 0, canvas.width, canvas.height);
      if (!canvas || !data || !canvas.height) return 0;
      let tallest = 0;
      for (let x = 0; x < data.width; x++) {
        let column = 0;
        for (let y = 0; y < data.height; y++) if (data.data[(y * data.width + x) * 4 + 3]! > 0) column++;
        tallest = Math.max(tallest, column);
      }
      return tallest / canvas.height;
    });
    await walk.open(project.path);
    await v.field.fill("Typed draft");
    const recognizers = () => page.evaluate("window.fixtureRecognizers.length");
    const focused = () => page.evaluate(() => document.activeElement?.getAttribute("aria-label") ?? document.activeElement?.tagName);

    // The operator's journey: dictate, cancel with the X, dismiss the keyboard, start again; repeated,
    // with one Escape. The X never focuses the field (on a phone that opens the keyboard), and each
    // restart listens on a live stream while the cancelled one has ended.
    for (const [cycle, cancelBy] of (["X", "X", "Escape", "X"] as const).entries()) {
      await v.mic.click();
      await expect(v.listening).toBeVisible();
      await expect(v.wave).toBeVisible();
      await expect.poll(() => page.evaluate(`window.fixtureStreams[${cycle}].getAudioTracks().every(track => track.readyState === "live" && track.enabled && !track.muted)`)).toBe(true);
      await expect.poll(loudest).toBeGreaterThan(0.5);
      await hear(page, [`discard ${cycle}`], "more");
      await expect(v.field).toHaveValue(`Typed draft discard ${cycle} more`);
      if (cancelBy === "X") {
        await walk.state(cycle === 0 ? "06-x-cancels-without-the-keyboard" : `06-x-cancels-again-${cycle}`, {
          action: () => v.cancel.click(),
          visible: [v.mic, v.field],
          hidden: [v.stop, v.cancel, v.wave],
        });
        await expect(v.field).not.toBeFocused();
        expect(await focused()).toBe("Start voice input");
        await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
      } else {
        await page.keyboard.press("Escape");
        await expect(v.field).toBeFocused();
      }
      await expect(v.field).toHaveValue("Typed draft");
      await expect(v.field).toBeEditable();
      expect(await recognizers()).toBe(cycle + 1);
      await expect.poll(() => page.evaluate(`window.fixtureRecognizers[${cycle}].ended`)).toBe(true);
      await expect.poll(() => page.evaluate(`window.fixtureStreams[${cycle}].getTracks().every(track => track.readyState === "ended")`)).toBe(true);
    }

    // A recognizer still shutting down: Cancel returns at once, and a restart opens the microphone
    // only once that recognizer has ended and its waveform audio context has closed.
    await page.evaluate("window.fixtureHoldAbort = true; window.fixtureHoldClose = true");
    await v.mic.click();
    await expect(v.listening).toBeVisible();
    await hear(page, ["gone at once"]);
    await walk.state("07-cancel-returns-while-the-recognizer-ends", {
      action: () => v.cancel.click(),
      visible: [v.mic, v.field],
      hidden: [v.stop, v.cancel, v.wave],
    });
    await expect(v.field).toHaveValue("Typed draft");
    await expect(v.field).toBeEditable();
    const opening = v.main.getByText("Opening microphone…", { exact: true });
    await walk.state("08-restart-waits-for-the-cancelled-recognizer", {
      action: () => v.mic.click(),
      visible: [opening, v.field],
      hidden: [v.mic, v.listening],
    });
    expect(await recognizers()).toBe(5);
    // The cancelled recognizer's stream stays open until it ends; the restart has not opened one yet.
    expect(await page.evaluate("window.fixtureStreams.length")).toBe(5);
    expect(await page.evaluate(`window.fixtureStreams[4].getTracks().every(track => track.readyState === "live")`)).toBe(true);
    expect(await page.evaluate("window.fixtureWaveforms[4].fixtureCloseCalled")).toBe(false);
    await page.evaluate("window.fixtureHoldAbort = false; window.fixtureRecognizers[4].end()");
    await expect.poll(() => page.evaluate(`window.fixtureStreams[4].getTracks().every(track => track.readyState === "ended")`)).toBe(true);
    await expect.poll(() => page.evaluate("window.fixtureWaveforms[4].fixtureCloseCalled")).toBe(true);
    await walk.state("08b-restart-waits-for-waveform-close", {
      visible: [opening, v.field, v.cancel],
      hidden: [v.mic, v.listening],
    });
    expect(await page.evaluate("window.fixtureStreams.length")).toBe(5);
    expect(await recognizers()).toBe(5);
    await walk.state("08c-cancel-while-waveform-closes", {
      action: () => v.cancel.click(),
      visible: [v.mic, v.field],
      hidden: [opening, v.cancel, v.listening],
    });
    await expect(v.field).toHaveValue("Typed draft");
    await expect(v.field).toBeEditable();
    await v.mic.click();
    await expect(opening).toBeVisible();
    expect(await page.evaluate("window.fixtureStreams.length")).toBe(5);
    await page.evaluate("window.fixtureHoldClose = false; window.fixtureWaveforms[4].fixtureReleaseClose()");
    await expect(v.listening).toBeVisible();
    await expect(opening).toBeHidden();
    await expect.poll(loudest).toBeGreaterThan(0.5);

    // The restarted capture ignores anything the cancelled recognizer still says.
    expect(await recognizers()).toBe(6);
    await page.evaluate(`{
      const old = window.fixtureRecognizers[4];
      old.onresult && old.onresult({ results: [{ isFinal: true, 0: { transcript: "stale words" }, length: 1 }] });
      old.onerror && old.onerror({ error: "not-allowed" });
      old.onend && old.onend();
    }`);
    await hear(page, ["fresh words"]);
    await walk.state("09-restarted-capture-unaffected", {
      visible: [v.listening, v.stop, v.cancel, v.field],
      hidden: [v.mic, v.main.getByText("Microphone blocked in the browser. Typing works.", { exact: true })],
    });
    await expect(v.field).toHaveValue("Typed draft fresh words");
    await walk.state("10-send-after-restart", {
      action: () => v.send.click(),
      visible: [v.bubble("Typed draft fresh words"), v.mic],
      hidden: [v.stop, v.cancel],
    });
    await expect(v.field).toHaveValue("");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0]!)).toMatchObject({ text: "Typed draft fresh words" });
  });

  test("browser recognition: task composer repeatedly cancels with X, restarts, and Stop preserves the typed draft", async ({ page, request }, info) => {
    const project = await fixtureProject(request);
    const task = await fixtureTask(request, project.name);
    const walk = walkthrough(page, info);
    const v = views(page, info);
    await browserBackend(page);
    await page.addInitScript(FAKE_RECOGNIZER);
    await page.route((url) => url.pathname === `/api/task/${project.name}/${task.slug}`,
      (route) => route.fulfill({ json: { ...task, state: "running" } }));
    await walk.open(`${project.path}/tasks/${task.slug}`);
    await v.field.fill("Task draft");
    for (let cycle = 0; cycle < 2; cycle++) {
      await walk.state(`task-${cycle}-listening`, {
        action: () => v.mic.click(),
        visible: [v.listening, v.field, v.wave, v.stop, v.cancel],
        hidden: [v.mic, v.transcribing],
      });
      await hear(page, [`discard ${cycle}`]);
      await expect(v.field).toHaveValue(`Task draft discard ${cycle}`);
      await walk.state(`task-${cycle}-x-discards`, {
        action: () => v.cancel.click(),
        visible: [v.mic, v.field],
        hidden: [v.stop, v.cancel, v.wave, v.transcribing],
      });
      await expect(v.field).toHaveValue("Task draft");
      await expect(v.field).toBeEditable();
      await expect(v.mic).toBeFocused();
    }
    await v.mic.click();
    await expect(v.listening).toBeVisible();
    await hear(page, ["fresh task words"]);
    await walk.state("task-restarted-stop-lands-words", {
      action: () => v.stop.click(),
      visible: [v.field, v.mic, v.send],
      hidden: [v.stop, v.cancel, v.wave, v.transcribing],
    });
    await expect(v.field).toHaveValue("Task draft fresh task words");
    await expect(v.field).toBeEditable();
    expect(await page.evaluate("window.fixtureRecognizers.length")).toBe(3);
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
    await expect(v.field).toHaveValue("Typing still works");
    await expect(v.field).toBeEditable();
    await expect(v.send).toBeEnabled();
    // A refusal may be transient (Safari's speech service briefly unavailable): the mic asks again.
    await walk.state("01b-denied-then-asks-again", {
      action: () => v.mic.click(),
      visible: [v.listening, v.stop, v.cancel],
      hidden: [v.main.getByText("Microphone blocked in the browser. Typing works.", { exact: true })],
    });
    await hear(page, ["asked again"]);
    await v.stop.click();
    await expect(v.field).toHaveValue("Typing still works asked again");

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
});
