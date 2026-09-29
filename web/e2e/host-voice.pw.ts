import { test } from "./fixtures";
import { expect, type Page, type TestInfo } from "@playwright/test";
import { fixtureProject } from "./fixture-data";
import { READY, fixtureHost } from "./hostVoice";
import { walkthrough } from "./walkthrough";

/*
 * Host voice (SPEC.md §3.6 and §3.15), walked at 390 and 1440. The page's real audio worklet turns the
 * browser's fake microphone into 16 kHz samples; the host is a page-level fixture that answers each
 * chunk with how many samples it has heard, so no model runs and nothing leaves the page. Its setup
 * states come from an overlaid /api/voice.
 */

/** Hold the worklet until the test releases it, so Starting voice stays on screen. */
const HOLD_WORKLET = `
  const add = AudioWorklet.prototype.addModule;
  AudioWorklet.prototype.addModule = async function (...args) {
    if (window.fixtureWorkletGate) await window.fixtureWorkletGate;
    return add.apply(this, args);
  };
  window.fixtureHoldWorklet = () => { window.fixtureWorkletGate = new Promise((resolve) => { window.fixtureReleaseWorklet = resolve; }); };
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
    listening: main.locator('.composer[data-phase="listening"]'),
    transcribing: main.getByText("Transcribing…", { exact: true }),
  };
}

test("host voice: Starting voice, live words, Transcribing, landed; Cancel and Send", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const host = await fixtureHost(page);
  await page.addInitScript(HOLD_WORKLET);
  await walk.open(project.path);
  await v.field.fill("Please");
  await page.evaluate(() => (window as unknown as { fixtureHoldWorklet(): void }).fixtureHoldWorklet());

  await walk.state("host-voice-01-starting", {
    action: () => v.mic.click(),
    visible: [v.stop, v.cancel, v.main.getByText("Starting voice…", { exact: true })],
    hidden: [v.mic, v.main.getByText("Listening… Stop to add text, or Send.", { exact: true })],
  });
  await walk.state("host-voice-02-listening", {
    action: () => page.evaluate(() => (window as unknown as { fixtureReleaseWorklet(): void }).fixtureReleaseWorklet()),
    visible: [v.listening, v.wave, v.main.getByText("Listening… Stop to add text, or Send.", { exact: true })],
    hidden: [v.main.getByText("Starting voice…", { exact: true })],
  });
  await expect(v.field).toHaveValue("Please check the build", { timeout: 5000 });
  await expect(v.field).toHaveAttribute("readonly", "");
  // The worklet sends 16 kHz samples: about half a second per request, one request at a time.
  expect(host.chunks[0]).toBeGreaterThanOrEqual(8000);
  expect(host.chunks[0]).toBeLessThan(16000);

  let release = () => {};
  host.holdFinal = new Promise((resolve) => { release = resolve; });
  await walk.state("host-voice-03-transcribing", {
    action: () => v.stop.click(),
    visible: [v.transcribing],
    hidden: [v.main.getByText("Listening… Stop to add text, or Send.", { exact: true })],
  });
  await walk.state("host-voice-04-landed", {
    action: async () => { release(); await expect(v.field).toHaveValue(/^Please Heard \d+ chunks\.$/); },
    visible: [v.mic, v.field],
    hidden: [v.transcribing, v.stop, v.cancel, v.wave, v.hint.filter({ hasText: /Transcribing|Listening|Voice stopped/ })],
  });
  await expect(v.field).not.toHaveAttribute("readonly", "");
  host.holdFinal = null;

  await v.mic.click();
  await expect(v.field).toHaveValue(/^Please Heard \d+ chunks\. check/, { timeout: 5000 });
  await walk.state("host-voice-05-cancelled", {
    action: () => v.cancel.click(),
    visible: [v.mic],
    hidden: [v.listening, v.wave, v.stop],
  });
  await expect(v.field).toHaveValue(/^Please Heard \d+ chunks\.$/);
  await expect.poll(() => host.requests.at(-1)).toBe(`live/${host.id}/cancel`);

  const sent: string[] = [];
  await page.route((url) => url.pathname === "/api/chat", async (route) => {
    sent.push(JSON.parse(route.request().postData() ?? "{}").text);
    await route.fulfill({ json: { ok: true } });
  });
  await v.field.fill("Send this");
  await v.mic.click();
  await expect(v.listening).toBeVisible();
  await expect(v.field).toHaveValue(/^Send this check/, { timeout: 5000 });
  await v.send.click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatch(/^Send this Heard \d+ chunks\.$/);
});

/** Every value the field shows over `ms`, one per animation frame, without repeats. */
function fieldValues(page: Page, ms: number) {
  return page.evaluate((duration) => new Promise<string[]>((resolve) => {
    const field = document.querySelector<HTMLTextAreaElement>("main textarea.composer-field")!;
    const seen = [field.value];
    const end = performance.now() + duration;
    const frame = () => {
      if (seen.at(-1) !== field.value) seen.push(field.value);
      if (performance.now() < end) requestAnimationFrame(frame);
      else resolve(seen);
    };
    requestAnimationFrame(frame);
  }), ms);
}

test("host voice: live words flow in at a steady pace; reduced motion shows them at once; Stop lands the whole text", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const host = await fixtureHost(page);
  const said = "check the build, then the timer, and after that the tests on both phones";
  host.live = "check the build,";
  host.final = `${said}.`;
  await walk.open(project.path);
  await v.field.fill("Please");
  await v.mic.click();
  await expect(v.field).toHaveValue("Please check the build,", { timeout: 5000 });

  // The next answer adds many words at once: they arrive letter by letter, each frame a longer prefix of them.
  host.live = said;
  const flowed = await fieldValues(page, 1500);
  const after = flowed.slice(flowed.indexOf("Please check the build,") + 1);
  expect(after.at(-1)).toBe(`Please ${said}`);
  expect(after.length).toBeGreaterThan(10);
  after.forEach((value, index) => {
    expect(`Please ${said}`.startsWith(value)).toBe(true);
    if (index) expect(value.length).toBeGreaterThan(after[index - 1].length);
  });
  await walk.state("host-voice-18-words-flowed-in", {
    visible: [v.listening, v.stop, v.cancel],
    hidden: [v.mic],
  });
  await walk.state("host-voice-19-stop-lands-whole-text", {
    action: () => v.stop.click(),
    visible: [v.mic, v.field],
    hidden: [v.listening, v.stop, v.cancel, v.transcribing],
  });
  await expect(v.field).toHaveValue(`Please ${said}.`);
  await expect(v.field).not.toHaveAttribute("readonly", "");

  await page.emulateMedia({ reducedMotion: "reduce" });
  await v.field.fill("Again");
  host.live = "check the build,";
  await v.mic.click();
  await expect(v.field).toHaveValue("Again check the build,", { timeout: 5000 });
  host.live = said;
  const instant = await fieldValues(page, 1500);
  expect(instant.filter((value) => value !== "Again check the build,")).toEqual([`Again ${said}`]);
  await walk.state("host-voice-20-reduced-motion-at-once", {
    visible: [v.listening, v.stop],
    hidden: [v.mic],
  });
  await v.cancel.click();
  await expect(v.field).toHaveValue("Again");
});

test("host voice: a stopped recording keeps its words and says why; busy says so", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const host = await fixtureHost(page);
  host.live = "check";
  await walk.open(project.path);
  await v.mic.click();
  await expect(v.field).toHaveValue("check", { timeout: 5000 });
  host.failAudio = { status: 503, error: "Voice stopped: the speech process stopped." };
  await walk.state("host-voice-06-stopped", {
    visible: [v.main.getByText("Voice stopped: the speech process stopped. Typing works.", { exact: true }), v.mic],
    hidden: [v.listening, v.wave, v.stop],
  });
  await expect(v.field).toHaveValue("check");
  await expect(v.field).not.toHaveAttribute("readonly", "");

  host.failAudio = null;
  host.refuse = { status: 429, error: "Voice is busy on another device." };
  await walk.state("host-voice-07-busy", {
    action: () => v.mic.click(),
    visible: [v.main.getByText("Voice is busy on another device. Typing works.", { exact: true }), v.mic],
    hidden: [v.listening, v.stop],
  });
  await expect(v.field).toHaveValue("check");
});

test("host voice not set up: the mic points to Settings, which sets it up with progress", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const host = await fixtureHost(page, { state: "absent", download_bytes: 698435338 });
  await walk.open(project.path);
  const setUp = v.main.getByRole("link", { name: "Set up voice", exact: true });
  await walk.state("host-voice-08-needs-setup", {
    action: () => v.mic.click(),
    visible: [v.main.getByText("Voice needs a one-time download on this computer.", { exact: false }), setUp, v.mic],
    hidden: [v.listening, v.stop],
  });
  expect(host.requests).toEqual([]);
  await setUp.click();
  const button = page.getByRole("button", { name: "Set up voice", exact: true });
  await walk.state("host-voice-09-settings-absent", {
    visible: [page.getByRole("radio", { name: "This computer", exact: true }), button,
      page.getByText("Needs a one-time download of about 698 MB, checked against this release.", { exact: true }),
      page.getByText("Speech model: NVIDIA Parakeet TDT 0.6B v2, licensed CC-BY-4.0.", { exact: true })],
    hidden: [page.getByRole("progressbar")],
  });
  await expect(page.getByRole("radio", { name: "This computer", exact: true })).toBeChecked();
  await walk.state("host-voice-10-setting-up", {
    action: () => button.click(),
    visible: [page.getByRole("progressbar", { name: "Voice setup" }), page.getByText("Setting up… 0 MB of 698 MB", { exact: true }),
      page.getByRole("button", { name: "Cancel setup", exact: true })],
    hidden: [button],
  });
  host.state = { state: "setting-up", download_bytes: 698435338, done_bytes: 420000000 };
  await expect(page.getByText("Setting up… 420 MB of 698 MB", { exact: true })).toBeVisible({ timeout: 5000 });
  host.state = READY;
  await walk.state("host-voice-11-ready", {
    visible: [page.getByText("Ready on this computer.", { exact: false }), page.getByRole("button", { name: "Remove voice (698 MB)", exact: true })],
    hidden: [page.getByRole("progressbar"), page.getByRole("button", { name: "Cancel setup", exact: true })],
  });
  expect(host.requests).toEqual(["setup"]);
});

test("host voice that cannot run here hides the mic and says why", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  await fixtureHost(page, { state: "unavailable", reason: "voice runs on Linux x86_64 only for now" });
  await walk.open(project.path);
  await walk.state("host-voice-12-unavailable", {
    visible: [v.main.getByText("Voice isn't available on this computer: voice runs on Linux x86_64 only for now. Typing works.", { exact: true }), v.field],
    hidden: [v.mic],
  });
});

test("host voice through a lost connection: keeps recording, catches up, waits after Stop, then gives up", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const host = await fixtureHost(page);
  await page.clock.install();
  await walk.open(project.path);
  const lost = v.main.getByText("Connection lost — still recording. Your words will catch up.", { exact: true });
  const catchingUp = v.main.getByText("Catching up…", { exact: true });
  const waiting = v.main.getByText("Waiting for connection…", { exact: true });
  const listeningHint = v.main.getByText("Listening… Stop to add text, or Send.", { exact: true });

  await v.field.fill("Draft");
  await v.mic.click();
  await expect(v.field).toHaveValue("Draft check the build", { timeout: 5000 });
  await walk.state("host-voice-13-connection-lost", {
    action: () => { host.offline = true; },
    visible: [lost, v.listening, v.stop, v.cancel],
    hidden: [listeningHint],
  });
  await expect(v.field).toHaveValue("Draft check the build");

  // The host restarted meanwhile: it no longer knows the recording, so the page replays it into a new one.
  let release = () => {};
  host.holdAudio = new Promise((resolve) => { release = resolve; });
  host.forget = true;
  await walk.state("host-voice-14-catching-up", {
    action: () => { host.offline = false; },
    visible: [catchingUp, v.listening],
    hidden: [lost, listeningHint],
  });
  await expect(v.field).toHaveValue("Draft check the build");
  expect(host.opened).toBe(2);
  release();
  host.holdAudio = null;
  await expect(listeningHint).toBeVisible({ timeout: 5000 });
  await expect(v.field).toHaveValue("Draft check the build");

  await walk.state("host-voice-15-waiting-for-connection", {
    action: async () => { host.offline = true; await v.stop.click(); },
    visible: [waiting, v.cancel],
    hidden: [v.stop, listeningHint],
  });
  await walk.state("host-voice-16-waiting-cancelled", {
    action: () => v.cancel.click(),
    visible: [v.mic, v.field],
    hidden: [waiting, v.cancel, v.wave],
  });
  await expect(v.field).toHaveValue("Draft");

  host.offline = false;
  await v.mic.click();
  await expect(v.field).toHaveValue("Draft check the build", { timeout: 5000 });
  const shown = await v.field.inputValue();
  host.offline = true;
  await v.stop.click();
  await expect(waiting).toBeVisible();
  await walk.state("host-voice-17-connection-not-back", {
    action: () => page.clock.fastForward(120_000),
    visible: [v.main.getByText("Couldn't reach this computer: your recording's last words weren't added.", { exact: true }), v.mic],
    hidden: [waiting, v.cancel],
  });
  await expect(v.field).toHaveValue(shown);
  await expect(v.field).not.toHaveAttribute("readonly", "");
});
