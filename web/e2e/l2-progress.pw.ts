import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "l2-progress-service.py" });

for (const index of [0, 1]) {
  test(`public activity and stopped-session steering with configured engine ${index + 1}`, async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const status = async () => (await (await request.get("/fixture/status")).json());
    const initial = (await status()).tasks[index];
    expect(initial, "Both configured engine fixtures are required").toBeTruthy();
    const slug = initial.slug;
    const task = async () => (await (await request.get(`/api/task/atlas/${slug}`)).json());
    const control = async (mode: string, text?: string) => {
      const response = await request.post("/fixture/control", { data: { slug, mode, text } });
      expect(response.ok()).toBe(true);
    };
    const path = `/projects/atlas/tasks/${slug}`;
    const convo = page.getByRole("region", { name: "Task conversation", exact: true });
    const preview = convo.getByRole("region", { name: "L2 activity" });
    const field = convo.getByRole("textbox", { name: "Message the L2" });
    const stop = convo.getByRole("button", { name: "Stop", exact: true });
    const next = "I am checking the message race.";
    await walk.open(path);
    await walk.state("01-running-public-direction", {
      visible: [preview.getByText("Latest from L2"), preview.getByText(/I am checking where pagination/), preview.getByText(/Recorded output changed/), stop, field],
      hidden: [page.getByRole("group", { name: "Stop this task?" })],
    });
    expect(await convo.locator('[data-role="l2"]').count()).toBe(1);
    await preview.getByRole("button", { name: "Expand" }).click();
    await walk.state("02-expanded-public-words", { visible: [preview.getByRole("button", { name: "Collapse" })], hidden: [preview.getByRole("button", { name: "Expand" })] });
    await control("output", next);
    await walk.state("03-new-direction-replaces-preview", { visible: [preview.getByText(next, { exact: true })], hidden: [preview.getByText(/I am checking where pagination/)] });
    expect(await convo.locator('[data-role="l2"]').count()).toBe(1);
    await control("quiet", next);
    await walk.state("04-quiet-is-not-progress", { visible: [preview.getByText("Last update"), preview.getByText(/No new activity for/)], hidden: [preview.getByText("Latest from L2")] });
    await control("unknown-time", next);
    await walk.state("05-prose-time-unavailable", { visible: [preview.getByText("Time unavailable")], hidden: [preview.getByText("Last update")] });
    await control("unavailable");
    await walk.state("06-unavailable-retains-labeled-last-update", { visible: [preview.getByText("Activity unavailable"), preview.getByText("Last known update."), preview.getByRole("button", { name: "Retry activity" })], hidden: [] });
    await field.fill("Keep the original page size.");
    await convo.getByRole("button", { name: "Send", exact: true }).click();
    await walk.state("07-steering-is-queued", { visible: [convo.getByText("Queued · waiting for a checkpoint", { exact: true })], hidden: [convo.getByText("Delivered to session", { exact: true })] });
    const before = await task();
    await field.fill("Use the existing retry rule.");
    await field.evaluate((element: HTMLTextAreaElement) => { element.setSelectionRange(4, 12); element.dispatchEvent(new Event("select", { bubbles: true })); });
    await convo.getByRole("button", { name: "View live session" }).click();
    const live = page.getByRole("region", { name: "Live session", exact: true });
    await walk.state("08-live-has-the-same-stop", { visible: [live, live.getByRole("button", { name: "Stop", exact: true })], hidden: info.project.name === "phone" ? [field] : [] });
    if (info.project.name === "phone") await page.getByRole("link", { name: "Conversation", exact: true }).click();
    await expect(field).toHaveValue("Use the existing retry rule.");
    expect(await field.evaluate((element: HTMLTextAreaElement) => [element.selectionStart, element.selectionEnd])).toEqual([4, 12]);
    await control("hold-stop");
    try {
      await stop.click();
      await walk.state("09-stopping-keeps-editable-draft", { visible: [convo.getByText("Stopping…", { exact: true }), field], hidden: [stop, convo.getByRole("button", { name: "Continue session" })] });
      await expect(convo.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
      await expect(field).toBeEnabled();
      // Another tab still thinks it is running: this message cannot release the Stop boundary.
      expect((await request.post("/api/l2/message", { data: { project: "atlas", slug, text: "Racing message stays held." } })).ok()).toBe(true);
    } finally { await control("release-stop"); }
    await expect.poll(async () => (await task()).steering.state).toBe("stopped");
    await walk.state("10-stopped-and-queued-messages-held", { visible: [convo.getByRole("button", { name: "Continue session" }), convo.getByText("Send a correction to continue this session."), convo.getByText("Queued · held until you continue").first()], hidden: [stop, preview] });
    expect((await status()).tasks.find((row: { slug: string }) => row.slug === slug).pending).toHaveLength(2);
    await control("hold-resume");
    try {
      await convo.getByRole("button", { name: "Send", exact: true }).click();
      await walk.state("11-correction-saved-waiting-for-resume", { visible: [convo.getByText("Waiting to resume", { exact: true })], hidden: [stop, convo.getByRole("button", { name: "Continue session" }), preview] });
      await expect(field).toHaveValue("");
    } finally { await control("release-resume"); }
    await expect.poll(async () => (await task()).state).toBe("running");
    await walk.state("12-resumed-awaits-new-output", { visible: [stop, preview.getByText("No public update yet."), convo.getByText("Delivered to session", { exact: true }).first()], hidden: [preview.getByText(next), convo.getByText("Waiting to resume", { exact: true })] });
    const resumed = await task();
    expect(resumed.session_id).toBe(before.session_id);
    expect(resumed.attempt).toBe(before.attempt);
    expect(resumed.l2_engine).toBe(initial.engine);
    expect(resumed.messages.filter((row: { text: string }) => row.text === "Use the existing retry rule.")).toHaveLength(1);
    const evidence = await status();
    expect(evidence.tasks.find((row: { slug: string }) => row.slug === slug).edit).toBe(initial.edit);
    const lastCall = evidence.calls.at(-1);
    expect(lastCall.session_id).toBe(before.session_id);
    expect(lastCall.prompt.indexOf("Keep the original page size.")).toBeLessThan(lastCall.prompt.indexOf("Racing message stays held."));
    expect(lastCall.prompt.indexOf("Racing message stays held.")).toBeLessThan(lastCall.prompt.indexOf("Use the existing retry rule."));
    await control("output", "The saved session has the correction.");
    await walk.state("13-new-session-output", { visible: [preview.getByText("The saved session has the correction.")], hidden: [preview.getByText("No public update yet.")] });
    await control("blocked");
    await walk.state("14-question-keeps-its-durable-place", { visible: [convo.locator(".decision-question").filter({ hasText: "Keep the original page size?" }), field], hidden: [preview, stop] });
    expect((await task()).question.status).toBe("open");
    await control("finished");
    await page.reload();
    await walk.state("15-finished-read-only", { visible: [convo.getByText("Pagination and steering are verified.")], hidden: [preview, field, stop] });
  });
}

test("Continue preserves an unsent draft; denied Stop and scoped Escape remain honest", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const initial = (await (await request.get("/fixture/status")).json()).tasks[0];
  const slug = initial.slug;
  const control = async (mode: string) => { expect((await request.post("/fixture/control", { data: { slug, mode } })).ok()).toBe(true); };
  const task = async () => (await (await request.get(`/api/task/atlas/${slug}`)).json());
  await walk.open(`/projects/atlas/tasks/${slug}`);
  const convo = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = convo.getByRole("textbox", { name: "Message the L2" });
  const stop = convo.getByRole("button", { name: "Stop", exact: true });
  await field.fill("An unsent thought.");
  await control("deny");
  await stop.click();
  await walk.state("01-stop-denied", { visible: [convo.getByRole("alert").filter({ hasText: "You do not have permission to stop" }), convo.getByRole("button", { name: "Check status" }), field], hidden: [convo.getByRole("button", { name: "Continue session" })] });
  await expect(field).toHaveValue("An unsent thought.");
  await expect(convo.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
  await control("allow");
  await convo.getByRole("button", { name: "Check status" }).click();
  await expect(stop).toBeVisible();
  await field.focus(); await page.keyboard.press("Escape");
  expect((await task()).steering.state).toBe("running");
  if (info.project.name === "desktop") {
    await page.setViewportSize({ width: 1100, height: 900 });
    const overlay = page.getByRole("dialog", { name: "Live session" });
    await walk.state("02-overlay-owns-escape", { visible: [overlay], hidden: [] });
    await page.keyboard.press("Escape"); await expect(overlay).toBeHidden();
    expect((await task()).steering.state).toBe("running");
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.locator("h1").click(); await page.keyboard.press("Escape");
  } else { await stop.click(); }
  await expect.poll(async () => (await task()).steering.state).toBe("stopped");
  await walk.state("03-stopped-draft-retained", { visible: [convo.getByRole("button", { name: "Continue session" })], hidden: [stop] });
  const saved = await task();
  await convo.getByRole("button", { name: "Continue session" }).click();
  await expect.poll(async () => (await task()).state).toBe("running");
  await walk.state("04-continue-keeps-unsent-draft", { visible: [stop, field], hidden: [convo.getByRole("button", { name: "Continue session" })] });
  await expect(field).toHaveValue("An unsent thought.");
  expect((await task()).messages).toEqual(saved.messages);
  expect((await task()).session_id).toBe(saved.session_id);
});

test("loading, compact activity, unconfirmed delivery and voice keep worker steering separate", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const initial = (await (await request.get("/fixture/status")).json()).tasks[0];
  const slug = initial.slug;
  const task = async () => (await (await request.get(`/api/task/atlas/${slug}`)).json());
  const control = async (mode: string) => { expect((await request.post("/fixture/control", { data: { slug, mode } })).ok()).toBe(true); };
  await page.addInitScript(() => {
    const state = window as typeof window & { denyFixtureMic?: boolean };
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", { configurable: true, value: async () => {
      if (state.denyFixtureMic) throw new DOMException("Denied by fixture", "NotAllowedError");
      const context = new AudioContext();
      await context.resume();
      const source = context.createOscillator();
      const output = context.createMediaStreamDestination();
      source.connect(output); source.start();
      return output.stream;
    } });
  });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`**/api/task/atlas/${slug}`, async (route) => { await gate; await route.continue(); }, { times: 1 });
  try {
    await walk.open(`/projects/atlas/tasks/${slug}`);
    await walk.state("01-reading-task", { visible: [page.getByLabel("Loading", { exact: true })], hidden: [page.getByRole("button", { name: "Stop", exact: true })] });
  } finally { release(); }
  const convo = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = convo.getByRole("textbox", { name: "Message the L2" });
  const preview = convo.getByRole("region", { name: "L2 activity" });
  const stop = convo.getByRole("button", { name: "Stop", exact: true });
  await expect(field).toBeVisible();
  const viewport = page.viewportSize()!;
  await page.setViewportSize({ width: viewport.width, height: 620 });
  await walk.state("02-short-viewport-folds-public-words", { visible: [preview.getByText("Latest from L2"), stop, field], hidden: [preview.locator(".task-activity-words"), preview.locator(".task-activity-age")] });
  await preview.getByRole("button", { name: "Expand" }).click();
  await walk.state("03-short-viewport-expands-public-words", { visible: [preview.locator(".task-activity-words"), stop], hidden: [] });
  await page.setViewportSize(viewport);
  await field.fill("A saved message without delivery evidence.");
  await convo.getByRole("button", { name: "Send", exact: true }).click();
  await expect(convo.getByText("Queued · waiting for a checkpoint")).toBeVisible();
  await control("unconfirmed-delivery");
  await walk.state("04-delivery-unconfirmed-without-receipt", { visible: [convo.getByText("Delivery unconfirmed")], hidden: [convo.getByText("Delivered to session"), convo.getByText("Queued · waiting for a checkpoint")] });
  await field.fill("Typed correction.");
  await convo.getByRole("button", { name: "Start voice input" }).click();
  await walk.state("05-listening-keeps-worker-stop-distinct", { visible: [convo.getByRole("button", { name: "Stop voice input" }), convo.getByRole("button", { name: "Cancel voice input" }), stop], hidden: [convo.getByRole("button", { name: "Start voice input" })] });
  if (info.project.name === "desktop") {
    await page.locator("h1").click();
    await page.keyboard.press("Escape");
  } else { await convo.getByRole("button", { name: "Cancel voice input" }).click(); }
  await walk.state("06-cancel-voice-keeps-worker-running", { visible: [convo.getByRole("button", { name: "Start voice input" }), stop], hidden: [convo.getByRole("button", { name: "Cancel voice input" })] });
  expect((await task()).steering.state).toBe("running");
  await expect(field).toHaveValue("Typed correction.");
  await convo.getByRole("button", { name: "Start voice input" }).click();
  await expect(convo.getByRole("button", { name: "Stop voice input" })).toBeVisible();
  await expect(convo.getByLabel("Recording time")).toHaveText(/0:0[1-9]/);
  await convo.getByRole("button", { name: "Stop voice input" }).click();
  await expect(field).toHaveValue("Typed correction. spoken correction");
  await walk.state("07-dictation-lands-only-in-draft", { visible: [field, stop], hidden: [convo.getByText("spoken correction", { exact: true }), convo.getByRole("button", { name: "Cancel voice input" })] });
  await page.evaluate(() => { (window as typeof window & { denyFixtureMic: boolean }).denyFixtureMic = true; });
  await convo.getByRole("button", { name: "Start voice input" }).click();
  await walk.state("08-microphone-denied-typing-still-works", { visible: [convo.getByText("Microphone blocked in the browser. Typing works."), field, stop], hidden: [convo.getByRole("button", { name: "Stop voice input" })] });
  await expect(field).toBeEnabled();
  await page.addInitScript(() => { Object.defineProperty(window, "MediaRecorder", { value: undefined, configurable: true }); });
  await page.reload();
  await walk.state("09-voice-unavailable-worker-stop-remains", { visible: [field, stop], hidden: [convo.getByRole("button", { name: "Start voice input" }), convo.getByRole("button", { name: "Stop voice input" })] });
});

for (const index of [0, 1]) {
  test(`activity cue and record times agree in Conversation and Live session with configured engine ${index + 1}`, async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const initial = (await (await request.get("/fixture/status")).json()).tasks[index];
    expect(initial, "Both configured engine fixtures are required").toBeTruthy();
    const slug = initial.slug;
    const control = async (mode: string, text?: string) => { expect((await request.post("/fixture/control", { data: { slug, mode, text } })).ok()).toBe(true); };
    const phone = info.project.name === "phone";
    const convo = page.getByRole("region", { name: "Task conversation", exact: true });
    const preview = convo.getByRole("region", { name: "L2 activity" });
    const cueDot = preview.locator(".task-activity-observation .dot");
    const live = page.getByRole("region", { name: "Live session", exact: true });
    const liveCue = live.getByRole("status");
    const toLive = async () => { if (phone) await convo.getByRole("button", { name: "View live session" }).click(); };
    const toConversation = async () => { if (phone) await page.getByRole("link", { name: "Conversation", exact: true }).click(); };
    const animation = (locator: typeof cueDot) => locator.evaluate((node) => getComputedStyle(node).animationName);
    await walk.open(`/projects/atlas/tasks/${slug}`);
    await control("output", "Running the pagination suite now.");
    await walk.state("01-conversation-working-cue", { visible: [preview.getByText(/Recorded output changed · \d+ sec ago/)], hidden: [preview.getByText(/No new activity/)] });
    await expect(cueDot).toHaveAttribute("data-pulse", "true");
    expect(await animation(cueDot)).toBe("voice-pulse");
    await convo.getByText("Activity & evidence").click();
    await walk.state("02-task-events-show-recorded-times", { visible: [convo.locator(".conversation-activity time.event-time").first()], hidden: [convo.locator(".conversation-activity .event-time[data-unavailable]")] });
    await toLive();
    await walk.state("03-live-working-cue-and-operation-times", {
      visible: [liveCue.getByText(/Recorded output changed · \d+ sec ago/), live.locator("article.session-reply time.session-time").last()],
      hidden: [liveCue.getByText(/No new activity/)],
    });
    await expect(live.locator(".live-pulse")).toHaveAttribute("data-tone", "live");
    await control("long-call");
    const call = live.locator("details.session-tool").filter({ hasText: "pnpm test --run" });
    await walk.state("04-live-long-operation-without-output", {
      visible: [call.getByText(/^running · 4 min$/), call.locator("time.session-time"), liveCue.getByText("No new activity for 4 min")],
      hidden: [liveCue.getByText(/Recorded output changed/)],
    });
    await expect(live.locator(".live-pulse")).toHaveAttribute("data-tone", "muted");
    await expect(liveCue.locator(".dot")).not.toHaveAttribute("data-pulse");
    await toConversation();
    await walk.state("05-conversation-quiet-matches-live", { visible: [preview.getByText("No new activity for 4 min"), preview.getByText("Last update")], hidden: [preview.getByText(/Recorded output changed/)] });
    await expect(cueDot).not.toHaveAttribute("data-pulse");
    await page.emulateMedia({ reducedMotion: "reduce" });
    await control("output", "The suite is still running.");
    await walk.state("06-reduced-motion-steady-working-dot", { visible: [preview.getByText(/Recorded output changed · \d+ sec ago/)], hidden: [preview.getByText(/No new activity/)] });
    await expect(cueDot).toHaveAttribute("data-pulse", "true");
    expect(await animation(cueDot)).toBe("none");
    await toLive();
    expect(await animation(live.locator(".live-pulse"))).toBe("none");
    await control("unknown-time", "A reply without a recorded time.");
    const untimed = live.locator("article.session-reply").filter({ hasText: "A reply without a recorded time." });
    await walk.state("07-live-operation-time-unavailable", { visible: [untimed.getByText("time unavailable", { exact: true })], hidden: [] });
    await control("unavailable");
    await walk.state("08-live-activity-unavailable-claims-nothing", { visible: [liveCue.getByText("Activity unavailable")], hidden: [liveCue.getByText(/Recorded output changed/)] });
    await expect(live.locator(".live-pulse")).toHaveAttribute("data-tone", "muted");
    await control("finished");
    await walk.open(`/projects/atlas/tasks/${slug}/live`);
    await walk.state("09-ended-session-has-no-cue", { visible: [live.getByText("Session ended")], hidden: [liveCue, live.locator(".live-pulse[data-tone=live]")] });
  });
}

for (const status of [409, 500, 200]) {
  test(`@phone-only send outcome ${status} survives switching to Live session`, async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const initial = (await (await request.get("/fixture/status")).json()).tasks[0];
    const convo = page.getByRole("region", { name: "Task conversation", exact: true });
    const field = convo.getByRole("textbox", { name: "Message the L2" });
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/l2/message", async (route) => {
      await gate;
      if (status === 200) return route.continue();
      // An unknown response may follow a successful storage append; the hint must not offer blind Retry.
      if (status === 500) expect((await route.fetch()).ok()).toBe(true);
      return route.fulfill({ status, json: { error: status === 409 ? "Message refused" : "Acceptance unknown" } });
    }, { times: 1 });
    await walk.open(`/projects/atlas/tasks/${initial.slug}`);
    await field.fill("Original steering message.");
    const posted = page.waitForRequest("**/api/l2/message");
    await convo.getByRole("button", { name: "Send", exact: true }).click();
    await posted;
    await field.fill("A newer unsent draft.");
    await convo.getByRole("button", { name: "View live session" }).click();
    try {
      await walk.state("01-send-pending-in-live-view", { visible: [page.getByRole("region", { name: "Live session", exact: true })], hidden: [field] });
    } finally {
      const arrived = page.waitForResponse("**/api/l2/message");
      release();
      await (await arrived).finished();
    }
    await page.getByRole("link", { name: "Conversation", exact: true }).click();
    await expect(field).toHaveValue(status === 200 ? "A newer unsent draft." : "Original steering message.\nA newer unsent draft.");
    await walk.state(status === 200 ? "02-accepted-send-keeps-only-new-draft" : status === 409 ? "02-refused-send-restores-both-drafts" : "02-unknown-send-restores-with-check-hint", {
      visible: [field, ...(status === 200 ? [convo.getByText("Original steering message.", { exact: true })] : [convo.locator(".composer-hint[role=alert]")])],
      hidden: status === 200 ? [convo.locator(".composer-hint[role=alert]")] : status === 500 ? [convo.getByRole("button", { name: "Retry", exact: true })] : [],
    });
    if (status === 409) await expect(convo.locator(".composer-hint")).toHaveText("Not sent. Retry");
    if (status === 500) await expect(convo.locator(".composer-hint")).toHaveText("Could not confirm delivery. Check the conversation before sending again.");
    const task = await (await request.get(`/api/task/atlas/${initial.slug}`)).json();
    expect(task.messages.filter((row: { text: string }) => row.text === "Original steering message.")).toHaveLength(status === 409 ? 0 : 1);
  });
}
