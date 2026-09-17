import { expect, type Page, type TestInfo } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ scenario: "isolation" });

test.setTimeout(60_000);

test("unavailable recovery storage keeps the message unsent and editable", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  await page.addInitScript(() => {
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith("altitude.submitted:")) throw new DOMException("Fixture storage full", "QuotaExceededError");
      return setItem.call(this, key, value);
    };
  });
  await walk.open(`${service}/projects/alpha`);
  await v.field("alpha").fill("Keep this sample draft");
  await v.send.click();
  await walk.state("01-recovery-storage-unavailable-unsent", {
    visible: [v.field("alpha"), v.convo.getByRole("alert").filter({ hasText: "Your message was not sent." })],
    hidden: [v.convo.locator(".bubble").filter({ hasText: "Keep this sample draft" })],
  });
  await expect(v.field("alpha")).toHaveValue("Keep this sample draft");
  const state = await (await request.get(`${service}/api/chat/alpha`)).json();
  expect(state.queued).toEqual([]);
  expect(state.history.filter((row: { role: string }) => row.role === "user")).toEqual([]);
  expect((await (await request.get(`${service}/fixture/calls`)).json()).calls).toEqual([]);
});

test("failed recovery updates preserve newer edits across navigation and explain the reload limit", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  await walk.open(`${service}/projects/alpha`);
  await page.route((url) => url.pathname === "/api/chat", (route) => route.abort("connectionfailed"), { times: 1 });
  await v.field("alpha").fill("Unconfirmed sample");
  await v.send.click();
  await expect(v.convo.getByRole("alert")).toContainText("Could not confirm delivery.");
  await page.evaluate(() => {
    const setItem = Storage.prototype.setItem;
    Object.defineProperty(window, "restoreRecoveryStorage", { value: () => { Storage.prototype.setItem = setItem; } });
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith("altitude.submitted:")) throw new DOMException("Fixture storage full", "QuotaExceededError");
      return setItem.call(this, key, value);
    };
  });
  await v.field("alpha").fill("Unconfirmed sample with newer edits");
  await switchProject(page, info, "beta");
  await switchProject(page, info, "alpha");
  await walk.state("01-newer-recovery-retained-with-write-warning", {
    visible: [v.field("alpha"), v.convo.getByRole("alert").filter({ hasText: "Keep this tab open" })], hidden: [v.retry],
  });
  await expect(v.field("alpha")).toHaveValue("Unconfirmed sample with newer edits");
  await page.evaluate("window.restoreRecoveryStorage()");
  await switchProject(page, info, "beta");
  await switchProject(page, info, "alpha");
  await page.reload();
  await walk.state("02-storage-restored-recovery-survives-reload", {
    visible: [v.field("alpha"), v.convo.getByRole("alert").filter({ hasText: "Could not confirm delivery." })],
    hidden: [v.retry, v.convo.getByText(/Keep this tab open/)],
  });
  await expect(v.field("alpha")).toHaveValue("Unconfirmed sample with newer edits");
  expect((await (await request.get(`${service}/fixture/calls`)).json()).calls).toEqual([]);
});

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

function views(page: Page) {
  const convo = page.getByRole("region", { name: "Conversation", exact: true });
  return {
    convo,
    field: (name: string) => page.getByRole("textbox", { name: `Message L3 about ${name}`, exact: true }),
    text: (text: string) => convo.getByText(text, { exact: true }),
    send: convo.getByRole("button", { name: "Send", exact: true }),
    queue: convo.getByRole("button", { name: "Queue", exact: true }),
    queued: convo.getByRole("list", { name: "Queued messages" }),
    retry: convo.getByRole("button", { name: "Retry", exact: true }),
    loading: convo.getByLabel("Loading", { exact: true }),
  };
}

async function switchProject(page: Page, info: TestInfo, name: string) {
  if (info.project.name === "phone") {
    await page.locator(".phone-title-button").click();
    await page.getByRole("dialog", { name: "Switch project" }).getByRole("link", { name, exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Switch project" })).toBeHidden();
  } else {
    await page.getByRole("navigation", { name: "Rail", exact: true }).getByRole("link", { name, exact: true }).click();
  }
  await expect(page).toHaveURL(new RegExp(`/projects/${name}$`));
  await expect(views(page).field(name)).toBeVisible();
}

test("project drafts survive selection and route remount with independent copy, paste and clearing", async ({ page, context, service }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  await walk.open(`${service}/projects/alpha`);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await v.field("alpha").fill("Alpha unsent draft");
  await walk.state("01-alpha-draft", { visible: [v.text("Alpha saved history."), v.field("alpha")], hidden: [v.loading] });
  const gate = deferred();
  await page.route((url) => url.pathname === "/api/chat/beta", async (route) => { await gate.promise; await route.continue(); }, { times: 1 });
  await switchProject(page, info, "beta");
  await walk.state("02-beta-loading-overlay", { visible: [v.loading, v.field("beta")], hidden: [v.text("Alpha saved history.")] });
  await expect(v.field("beta")).toHaveValue("");
  gate.release();
  await walk.state("03-beta-empty", { visible: [v.text("Say what you want done. L3 answers or creates one task.")], hidden: [v.loading, v.text("Alpha saved history.")] });
  await v.field("beta").fill("Beta independent draft");
  await switchProject(page, info, "alpha");
  await expect(v.field("alpha")).toHaveValue("Alpha unsent draft");
  await v.field("alpha").press("Control+a");
  await page.keyboard.press("Control+c");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("Alpha unsent draft");
  await walk.state("04-alpha-draft-restored", { visible: [v.text("Alpha saved history."), v.field("alpha")], hidden: [v.text("Beta independent draft")] });
  await switchProject(page, info, "beta");
  await expect(v.field("beta")).toHaveValue("Beta independent draft");
  await v.field("beta").press("End");
  await page.keyboard.press("Shift+Enter");
  await page.keyboard.press("Control+v");
  await expect(v.field("beta")).toHaveValue("Beta independent draft\nAlpha unsent draft");
  const nav = page.getByRole("navigation", { name: info.project.name === "phone" ? "Primary" : "Rail", exact: true });
  await nav.getByRole("link", { name: "Monitor", exact: true }).click();
  await walk.state("05-route-unmounted", { visible: [page.getByRole("heading", { name: "Monitor", exact: true })], hidden: [v.convo] });
  await nav.getByRole("link", { name: info.project.name === "phone" ? "Chat" : "beta", exact: true }).click();
  await expect(v.field("beta")).toHaveValue("Beta independent draft\nAlpha unsent draft");
  await walk.state("06-pasted-draft-survives-remount", { visible: [v.field("beta")], hidden: [v.text("Alpha saved history.")] });
  await v.field("beta").fill("");
  await switchProject(page, info, "alpha");
  await expect(v.field("alpha")).toHaveValue("Alpha unsent draft");
  await switchProject(page, info, "beta");
  await expect(v.field("beta")).toHaveValue("");
  await walk.state("07-manually-cleared-draft-stays-cleared", { visible: [v.field("beta")], hidden: [v.text("Alpha saved history.")] });
});

for (const first of ["alpha", "beta"]) {
  test(`pending, streaming, concurrent sends and queue stay owned when ${first} finishes first`, async ({ page, request, service }, info) => {
    const walk = walkthrough(page, info);
    const v = views(page);
    const read = async (name: string) => (await (await request.get(`${service}/api/chat/${name}`)).json());
    await walk.open(`${service}/projects/alpha`);
    const gate = deferred();
    await page.route((url) => url.pathname === "/api/chat", async (route) => { await gate.promise; await route.continue(); }, { times: 1 });
    await v.field("alpha").fill("Alpha running request");
    await v.send.click();
    await walk.state("01-alpha-pending-overlay", { visible: [v.text("Alpha running request"), v.convo.locator(".msg-row[data-pending]")], hidden: [v.text("Alpha partial reply.")] });
    if (first === "beta") {
      gate.release();
      await walk.state("01b-alpha-streaming-before-switch", { visible: [v.text("Alpha partial reply."), v.queue], hidden: [v.convo.locator(".msg-row[data-pending]")] });
    }
    await switchProject(page, info, "beta");
    await v.field("beta").fill("Beta draft while Alpha starts");
    await walk.state("02-beta-after-pending", { visible: [v.field("beta"), v.send], hidden: [v.text("Alpha running request"), v.convo.locator(".msg-row[data-pending]")] });
    gate.release();
    await expect.poll(async () => (await read("alpha")).active?.trigger).toBe("chat");
    await expect(v.field("beta")).toHaveValue("Beta draft while Alpha starts");
    await switchProject(page, info, "alpha");
    await walk.state("03-alpha-active-from-history", { visible: [v.text("Alpha running request"), v.queue], hidden: [v.text("Beta draft while Alpha starts")] });
    await v.field("alpha").fill("Alpha queued request");
    await v.queue.click();
    await walk.state("04-alpha-durable-queue", { visible: [v.queued, v.queued.getByText("Alpha queued request", { exact: true })], hidden: [v.text("Beta running request")] });
    expect((await read("alpha")).queued.map((row: { text: string }) => row.text)).toEqual(["Alpha queued request"]);
    await switchProject(page, info, "beta");
    await v.field("beta").fill("Beta running request");
    await v.send.click();
    await walk.state("05-beta-streaming", { visible: [v.text("Beta running request"), v.text("Beta partial reply."), v.queue], hidden: [v.text("Alpha running request"), v.queued] });
    await v.field("beta").fill("Beta keeps its next draft");
    const second = first === "alpha" ? "beta" : "alpha";
    expect((await request.post(`${service}/fixture/release/${first}`)).ok()).toBe(true);
    await expect.poll(async () => (await read(first)).busy).toBe(false);
    await expect(v.field("beta")).toHaveValue("Beta keeps its next draft");
    await walk.state(`06-${first}-completed-beta-owned`, { visible: [v.field("beta"), v.text(first === "beta" ? "Beta running request answered." : "Beta partial reply.")], hidden: [v.text("Alpha running request answered."), v.text("Alpha queued request answered."), v.retry] });
    expect((await request.post(`${service}/fixture/release/${second}`)).ok()).toBe(true);
    await expect.poll(async () => (await read("alpha")).history.some((row: { text: string }) => row.text === "Alpha queued request answered.")).toBe(true);
    await expect.poll(async () => (await read("beta")).busy).toBe(false);
    await expect(v.field("beta")).toHaveValue("Beta keeps its next draft");
    await walk.state("07-beta-completed", { visible: [v.text("Beta running request answered.")], hidden: [v.text("Alpha running request answered."), v.queued] });
    await switchProject(page, info, "alpha");
    await walk.state("08-alpha-returned-history", { visible: [v.text("Alpha saved history."), v.text("Alpha running request answered."), v.text("Alpha queued request answered.")], hidden: [v.text("Beta running request"), v.text("Beta running request answered."), v.queued] });
    await expect(v.field("alpha")).toHaveValue("");
    await switchProject(page, info, "beta");
    await expect(v.field("beta")).toHaveValue("Beta keeps its next draft");
    await walk.state("09-beta-returned-history", { visible: [v.text("Beta running request answered.")], hidden: [v.text("Alpha saved history.")] });
    for (const name of ["alpha", "beta"]) {
      const rows = (await read(name)).history as { text: string }[];
      expect(rows.some((row) => row.text.startsWith(name === "alpha" ? "Beta" : "Alpha"))).toBe(false);
      const project = await (await request.get(`${service}/api/project/${name}`)).json();
      expect(project.l3.session_id).toBe(`fixture-${name}-session`);
    }
  });
}

test("late refused sends leave Beta alone and a visible refusal retries only in Alpha", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  await walk.open(`${service}/projects/alpha`);
  const gate = deferred();
  const refused = deferred();
  await page.route((url) => url.pathname === "/api/chat", async (route) => {
    expect(route.request().postDataJSON().project).toBe("alpha");
    await gate.promise;
    await route.fulfill({ status: 409, json: { error: "Controlled refusal" } });
    refused.release();
  }, { times: 1 });
  await v.field("alpha").fill("Alpha refused request");
  await v.send.click();
  await switchProject(page, info, "beta");
  await v.field("beta").fill("Beta untouched draft");
  gate.release();
  await refused.promise;
  await expect(v.field("beta")).toHaveValue("Beta untouched draft");
  await walk.state("01-beta-after-late-refusal-overlay", { visible: [v.field("beta"), v.send], hidden: [v.retry, v.convo.getByRole("alert"), v.text("Alpha refused request")] });
  await switchProject(page, info, "alpha");
  await expect(v.field("alpha")).toHaveValue("Alpha refused request");
  await walk.state("02-alpha-refused-submission-recovered", { visible: [v.text("Alpha saved history."), v.retry, v.convo.getByRole("alert").filter({ hasText: "Not sent." })], hidden: [v.convo.locator(".bubble").filter({ hasText: "Alpha refused request" })] });
  await page.route((url) => url.pathname === "/api/chat", (route) => route.fulfill({ status: 409, json: { error: "Controlled refusal" } }), { times: 1 });
  await v.field("alpha").fill("Alpha retry request");
  await v.send.click();
  await walk.state("03-alpha-refused-overlay", { visible: [v.convo.getByRole("alert").filter({ hasText: "Not sent." }), v.retry], hidden: [v.convo.locator(".bubble").filter({ hasText: "Alpha retry request" })] });
  await expect(v.field("alpha")).toHaveValue("Alpha retry request");
  await v.retry.click();
  await walk.state("04-alpha-retry-answered", { visible: [v.text("Alpha retry request answered.")], hidden: [v.retry, v.convo.getByRole("alert")] });
  const calls = (await (await request.get(`${service}/fixture/calls`)).json()).calls;
  expect(calls.map((row: { project: string; text: string }) => [row.project, row.text])).toEqual([["alpha", "Alpha retry request"]]);
});

test("a late accepted failure is stored in Alpha and its retry remains in Alpha", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  await walk.open(`${service}/projects/alpha`);
  await v.field("alpha").fill("Alpha accepted failure");
  await v.send.click();
  await expect.poll(async () => (await (await request.get(`${service}/api/chat/alpha`)).json()).active?.trigger).toBe("chat");
  await switchProject(page, info, "beta");
  await v.field("beta").fill("Beta error-free draft");
  expect((await request.post(`${service}/fixture/release/alpha`)).ok()).toBe(true);
  await expect.poll(async () => (await (await request.get(`${service}/api/chat/alpha`)).json()).history.some((row: { role: string }) => row.role === "error")).toBe(true);
  await expect(v.field("beta")).toHaveValue("Beta error-free draft");
  await walk.state("01-beta-after-alpha-error", { visible: [v.field("beta"), v.send], hidden: [v.retry, v.text("Alpha accepted failure"), v.convo.getByText("L3 could not answer this turn.", { exact: false })] });
  await switchProject(page, info, "alpha");
  await walk.state("02-alpha-failed-turn", { visible: [v.text("Alpha accepted failure"), v.retry, v.convo.getByText("L3 could not answer this turn.", { exact: false })], hidden: [v.text("Beta error-free draft")] });
  await v.retry.click();
  await walk.state("03-alpha-accepted-retry-answered", { visible: [v.text("Alpha accepted failure answered.")], hidden: [v.text("Beta error-free draft")] });
  const calls = (await (await request.get(`${service}/fixture/calls`)).json()).calls;
  expect(calls.map((row: { project: string; text: string }) => [row.project, row.text])).toEqual([["alpha", "Alpha accepted failure"], ["alpha", "Alpha accepted failure"]]);
  expect((await (await request.get(`${service}/api/chat/beta`)).json()).history).toEqual([]);
});

for (const nextDraft of ["", "A new draft while the accepted turn answers"]) {
  test(`an accepted turn survives a broken stream and failed refresh with ${nextDraft ? "a new" : "an empty"} draft`, async ({ page, request, service }, info) => {
    const walk = walkthrough(page, info);
    const v = views(page);
    const read = async () => (await (await request.get(`${service}/api/chat/alpha`)).json());
    const alert = v.convo.getByRole("alert").filter({ hasText: "Not sent." });
    let offline = false;
    const failedRead = deferred();
    await page.route((url) => url.pathname === "/api/chat/alpha", async (route) => {
      if (!offline) return route.continue();
      await route.abort("connectionfailed");
      failedRead.release();
    });
    await walk.open(`${service}/projects/alpha`);
    await v.field("alpha").fill("Alpha disconnected request");
    await v.send.click();
    await walk.state("01-turn-accepted-before-disconnect", {
      visible: [v.text("Alpha disconnected request"), v.queue],
      hidden: [v.convo.locator(".msg-row[data-pending]"), alert, v.retry],
    });
    const accepted = await read();
    const turnId = accepted.active.id;
    expect(accepted.history.filter((row: { role: string; turn_id: string }) => row.role === "user" && row.turn_id === turnId)).toHaveLength(1);
    await expect(v.field("alpha")).toHaveValue("");
    if (nextDraft) await v.field("alpha").fill(nextDraft);
    offline = true;
    expect((await request.post(`${service}/fixture/disconnect`)).ok()).toBe(true);
    await failedRead.promise;
    await expect(v.field("alpha")).toHaveValue(nextDraft);
    await walk.state("02-broken-stream-refresh-failed-still-sent", {
      visible: [v.text("Alpha disconnected request"), v.field("alpha")],
      hidden: [alert, v.retry, v.convo.locator(".msg-row[data-pending]")],
    });
    offline = false;
    await page.reload();
    await walk.state("03-reconnected-active-turn", {
      visible: [v.text("Alpha disconnected request"), v.queue, v.convo.getByRole("status", { name: "L3 is answering", exact: true })],
      hidden: [alert, v.retry],
    });
    await expect(v.field("alpha")).toHaveValue("");
    await v.field("alpha").fill("Alpha queued after reconnect");
    await v.queue.click();
    await walk.state("04-accepted-busy-queue", {
      visible: [v.queued.getByText("Alpha queued after reconnect", { exact: true })],
      hidden: [alert, v.retry],
    });
    await expect(v.field("alpha")).toHaveValue("");
    const queued = (await read()).queued;
    expect(queued).toHaveLength(1);
    const queueId = queued[0].id;
    await page.reload();
    await walk.state("05-queue-survives-reconnect", {
      visible: [v.text("Alpha disconnected request"), v.queued.getByText("Alpha queued after reconnect", { exact: true })],
      hidden: [alert, v.retry],
    });
    expect((await read()).queued.map((row: { id: string }) => row.id)).toEqual([queueId]);
    expect((await request.post(`${service}/fixture/release/alpha`)).ok()).toBe(true);
    await walk.state("06-disconnected-and-queued-turns-answer-once", {
      visible: [v.text("Alpha disconnected request answered."), v.text("Alpha queued after reconnect answered.")],
      hidden: [v.queued, alert, v.retry],
    });
    const final = await read();
    expect(final.history.filter((row: { role: string; turn_id: string }) => row.role === "user" && row.turn_id === turnId)).toHaveLength(1);
    const calls = (await (await request.get(`${service}/fixture/calls`)).json()).calls;
    expect(calls.map((row: { text: string }) => row.text)).toEqual(["Alpha disconnected request", "Alpha queued after reconnect"]);
  });
}

test("listening, late Stop transcription and denied microphone reset without crossing project drafts", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  // Browser overlay: real MediaRecorder records a synthetic tone; no device or speech service is used.
  await page.addInitScript(`
    const context = new AudioContext();
    const oscillator = context.createOscillator();
    oscillator.start();
    window.fixtureStreams = [];
    window.fixtureDenied = false;
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", { configurable: true, value: async () => {
      if (window.fixtureDenied) throw new DOMException("Denied by fixture", "NotAllowedError");
      await context.resume();
      const destination = context.createMediaStreamDestination();
      oscillator.connect(destination);
      window.fixtureStreams.push(destination.stream);
      return destination.stream;
    }});
  `);
  await walk.open(`${service}/projects/alpha`);
  await v.field("alpha").fill("Alpha preexisting draft");
  const mic = v.convo.getByRole("button", { name: "Start voice input" });
  const stop = v.convo.getByRole("button", { name: "Stop voice input" });
  const wave = v.convo.locator(".composer-wave");
  await mic.click();
  await walk.state("01-alpha-listening-overlay", { visible: [stop, wave], hidden: [mic] });
  await switchProject(page, info, "beta");
  await walk.state("02-beta-listening-ui-reset-overlay", { visible: [mic, v.field("beta")], hidden: [stop, wave] });
  await switchProject(page, info, "alpha");
  await expect(v.field("alpha")).toHaveValue("Alpha preexisting draft");
  const transcript = deferred();
  const uploaded = deferred();
  const delivered = deferred();
  await page.route((url) => url.pathname === "/api/transcribe", async (route) => {
    uploaded.release();
    await transcript.promise;
    await route.fulfill({ json: { text: "Alpha late transcript" } }).catch(() => {}); // navigation aborts the upload
    delivered.release();
  }, { times: 1 });
  await mic.click();
  await expect(stop).toBeVisible();
  await page.waitForTimeout(500); // MediaRecorder needs a non-empty audio chunk.
  await stop.click();
  await uploaded.promise;
  await walk.state("03-alpha-transcribing-overlay", { visible: [v.text("Transcribing…"), v.field("alpha"), ...(info.project.name === "phone" ? [] : [wave])], hidden: [stop, ...(info.project.name === "phone" ? [wave] : [])] });
  await expect(v.field("alpha")).not.toBeEditable();
  await switchProject(page, info, "beta");
  await v.field("beta").fill("Beta typed during transcription");
  transcript.release();
  await delivered.promise;
  await expect(v.field("beta")).toHaveValue("Beta typed during transcription");
  await walk.state("04-beta-late-transcript-ignored-overlay", { visible: [mic, v.field("beta")], hidden: [wave, v.text("Transcribing…"), v.text("Alpha late transcript")] });
  await switchProject(page, info, "alpha");
  await expect(v.field("alpha")).toHaveValue("Alpha preexisting draft");
  await expect(v.field("alpha")).toBeEditable();
  await page.evaluate("window.fixtureDenied = true");
  await mic.click();
  const denied = v.text("Microphone blocked in the browser. Typing works.");
  await walk.state("05-alpha-denied-overlay", { visible: [denied, mic], hidden: [wave, stop] });
  await expect(mic).toBeDisabled();
  await switchProject(page, info, "beta");
  await expect(mic).toBeEnabled();
  await expect(v.field("beta")).toHaveValue("Beta typed during transcription");
  await walk.state("06-beta-microphone-state-reset-overlay", { visible: [mic, v.field("beta")], hidden: [denied, wave, stop] });
  await expect.poll(() => page.evaluate("window.fixtureStreams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))")).toBe(true);
  expect((await (await request.get(`${service}/fixture/calls`)).json()).calls).toEqual([]);
  expect((await (await request.get(`${service}/api/chat/beta`)).json()).history).toEqual([]);
});

for (const result of ["success", "failure", "cancel"] as const) {
  test(`voice Send retains its original project through navigation and ${result}`, async ({ page, request, service }, info) => {
    const walk = walkthrough(page, info);
    const v = views(page);
    await page.addInitScript(`
      const context = new AudioContext();
      const oscillator = context.createOscillator(); oscillator.start();
      Object.defineProperty(navigator.mediaDevices, "getUserMedia", { configurable: true, value: async () => {
        await context.resume(); const destination = context.createMediaStreamDestination(); oscillator.connect(destination); return destination.stream;
      }});
      // Encode real audio, then defer the recorder completion callback until after navigation.
      const stopped = Object.getOwnPropertyDescriptor(MediaRecorder.prototype, "onstop");
      Object.defineProperty(MediaRecorder.prototype, "onstop", { configurable: true,
        get: stopped.get,
        set(handler) { stopped.set.call(this, event => { window.fixtureFinishRecorder = () => handler.call(this, event); }); },
      });
    `);
    const transcript = deferred();
    const uploaded = deferred();
    const delivered = deferred();
    let transcriptions = 0;
    // Keep interception active while completing transcription starts the message POST.
    await page.route("**/api/transcribe", async (route) => {
      transcriptions++;
      uploaded.release();
      await transcript.promise;
      await route.fulfill(result === "failure"
        ? { status: 503, json: { error: "Deterministic transcription failure" } }
        : { json: { text: "dictated instruction" } }).catch(() => {});
      delivered.release();
    });
    const posts: { project: string; text: string }[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/chat" && request.method() === "POST") posts.push(request.postDataJSON());
    });
    await walk.open(`${service}/projects/alpha`);
    await v.field("alpha").fill("Alpha original draft");
    await v.convo.getByRole("button", { name: "Start voice input" }).click();
    await expect(v.convo.getByRole("button", { name: "Stop voice input" })).toBeVisible();
    await page.waitForTimeout(500);
    await v.send.click();
    await switchProject(page, info, "beta");
    await v.field("beta").fill("Beta independent draft");
    await expect.poll(() => page.evaluate("typeof window.fixtureFinishRecorder")).toBe("function");
    await page.evaluate("window.fixtureFinishRecorder()");
    await uploaded.promise;
    await walk.state("01-navigation-before-recorder-stop-stays-in-source", {
      visible: [v.field("beta")], hidden: [v.text("Transcribing…"), v.text("Alpha original draft")],
    });
    await switchProject(page, info, "alpha");
    await expect(v.field("alpha")).toHaveValue("Alpha original draft");
    await expect(v.field("alpha")).not.toBeEditable();
    const cancel = v.convo.getByRole("button", { name: "Cancel voice input" });
    await walk.state("02-source-return-shows-pending-send", {
      visible: [v.field("alpha"), cancel, v.text("Transcribing…")], hidden: [],
    });
    await expect(v.send).toBeDisabled();
    if (result === "cancel") {
      await cancel.click();
      await expect(v.field("alpha")).toHaveValue("Alpha original draft");
      await expect(v.field("alpha")).toBeEditable();
      await v.field("alpha").fill("Alpha edited after cancellation");
    }
    await switchProject(page, info, "beta");
    transcript.release();
    await delivered.promise;
    if (result === "success") {
      await expect.poll(async () => (await (await request.get(`${service}/api/chat/alpha`)).json()).history.filter((row: { role: string; text: string }) => row.role === "user" && row.text === "Alpha original draft dictated instruction").length).toBe(1);
    }
    await expect(v.field("beta")).toHaveValue("Beta independent draft");
    await switchProject(page, info, "alpha");
    await expect(v.field("alpha")).toHaveValue(result === "success" ? "" : result === "cancel" ? "Alpha edited after cancellation" : "Alpha original draft");
    await expect(v.field("alpha")).toBeEditable();
    await walk.state(`03-original-conversation-${result}`, {
      visible: [v.field("alpha"), ...(result === "success" ? [v.text("Alpha original draft dictated instruction")] : result === "failure" ? [v.convo.getByRole("alert").filter({ hasText: "Could not transcribe" })] : [])],
      hidden: [v.text("Transcribing…"), cancel],
    });
    expect(transcriptions).toBe(1);
    expect(posts.map(({ project, text }) => ({ project, text }))).toEqual(result === "success" ? [{ project: "alpha", text: "Alpha original draft dictated instruction" }] : []);
    expect((await (await request.get(`${service}/fixture/calls`)).json()).calls.map((row: { project: string; text: string }) => ({ project: row.project, text: row.text }))).toEqual(posts.map(({ project, text }) => ({ project, text })));
    expect((await (await request.get(`${service}/api/chat/beta`)).json()).history).toEqual([]);
  });
}

test("immediate navigation preserves ordered L3 admission, queue and active history", async ({ page, request, context, service }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  const read = async (project = "alpha") => (await (await request.get(`${service}/api/chat/${project}`)).json());
  const nav = page.getByRole("navigation", { name: info.project.name === "phone" ? "Primary" : "Rail", exact: true });
  const first = "Alpha first navigation request";
  const second = "Alpha second navigation request";
  await walk.open(`${service}/projects/alpha`);
  await v.field("alpha").fill("Alpha running request");
  await v.send.click();
  await expect(v.queue).toBeVisible();

  for (const [index, text] of [first, second].entries()) {
    const arrived = deferred();
    const gate = deferred();
    const delivered = deferred();
    await page.route((url) => url.pathname === "/api/chat", async (route) => {
      // First delay is before admission; second delay is after real durable queue admission.
      if (index === 0) { arrived.release(); await gate.promise; }
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      if (index === 1) { arrived.release(); await gate.promise; }
      await route.fulfill({ response });
      delivered.release();
    }, { times: 1 });
    await v.field("alpha").fill(text);
    await v.queue.click();
    await arrived.promise;
    if (index === 0) {
      if (info.project.name === "phone") {
        await nav.getByRole("link", { name: "Work", exact: true }).click();
        await walk.state("01-work-before-admission", { visible: [page.getByRole("region", { name: "Work", exact: true })], hidden: [v.convo] });
      }
      await nav.getByRole("link", { name: "Needs you", exact: true }).click();
      await walk.state("02-needs-you-before-admission", { visible: [page.getByRole("heading", { name: "Needs you", exact: true })], hidden: [v.convo] });
      await nav.getByRole("link", { name: "Monitor", exact: true }).click();
      await walk.state("03-monitor-before-admission", { visible: [page.getByRole("heading", { name: "Monitor", exact: true })], hidden: [v.convo] });
      await nav.getByRole("link", { name: info.project.name === "phone" ? "Chat" : "alpha", exact: true }).click();
    }
    await switchProject(page, info, "beta");
    await v.field("beta").fill("Beta draft stays local");
    expect((await read()).queued.map((row: { text: string }) => row.text)).toEqual(index === 0 ? [] : [first, second]);
    gate.release();
    await delivered.promise;
    await expect(v.field("beta")).toHaveValue("Beta draft stays local");
    await walk.state(`04-${index}-destination-isolated`, { visible: [v.field("beta")], hidden: [v.queued, v.text(text), v.retry] });
    await switchProject(page, info, "alpha");
    await walk.state(`05-${index}-returned-queue`, { visible: [v.queued.getByText(text, { exact: true })], hidden: [v.retry] });
    await expect(v.field("alpha")).toHaveValue("");
  }
  const ids = (await read()).queued.map((row: { id: string }) => row.id);
  expect(ids).toHaveLength(2);
  await v.field("alpha").fill("Explicitly removed navigation request");
  await v.queue.click();
  const removed = v.queued.getByRole("listitem").filter({ hasText: "Explicitly removed navigation request" });
  await removed.getByRole("button", { name: "Remove", exact: true }).click();
  await expect(removed).toBeHidden();
  await page.reload();
  await walk.state("06-reloaded-ordered-queue", { visible: [v.queued.getByText(first, { exact: true }), v.queued.getByText(second, { exact: true })], hidden: [v.retry] });
  expect((await read()).queued.map((row: { id: string }) => row.id)).toEqual(ids);

  const otherTab = await context.newPage();
  await otherTab.goto(`${service}/projects/beta`);
  await otherTab.bringToFront();
  // Chromium headless keeps tabs visible; dispatch the browser's background/foreground signals.
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" }); document.dispatchEvent(new Event("visibilitychange")); });
  expect((await request.post(`${service}/fixture/release/alpha`)).ok()).toBe(true);
  const combined = `${first}\n\n${second}`;
  await expect.poll(async () => (await read()).history.some((row: { role: string; text: string }) => row.role === "user" && row.text === combined)).toBe(true);
  const active = await read();
  expect(active.queued).toEqual([]);
  expect(active.active).toBeTruthy();
  await page.bringToFront();
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); document.dispatchEvent(new Event("visibilitychange")); window.dispatchEvent(new Event("focus")); });
  await walk.state("07-foreground-active-history", { visible: [v.convo.locator(".bubble").filter({ hasText: first }), v.convo.getByRole("status", { name: "L3 is answering", exact: true })], hidden: [v.queued, v.retry] });
  await page.reload();
  await walk.state("08-reloaded-active-history", { visible: [v.convo.locator(".bubble").filter({ hasText: second }), v.queue], hidden: [v.queued, v.retry] });
  expect((await read()).active.id).toBe(active.active.id);
  expect((await request.post(`${service}/fixture/release-queued`)).ok()).toBe(true);
  await expect(v.text(`${second} answered.`)).toBeVisible({ timeout: 25_000 });
  await walk.state("09-completed-history-without-navigation-refresh", { visible: [v.text(`${second} answered.`)], hidden: [v.queued, v.retry, v.convo.getByRole("status", { name: "L3 is answering", exact: true })] });
  const calls = (await (await request.get(`${service}/fixture/calls`)).json()).calls;
  expect(calls.map((row: { text: string }) => row.text)).toEqual(["Alpha running request", combined]);
  expect((await read()).history.filter((row: { role: string; text: string }) => row.role === "user" && row.text === combined)).toHaveLength(1);
  expect((await read("beta")).history).toEqual([]);
  await otherTab.close();
});

test("lost L3 queue receipt survives navigation and reload without automatic resend", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  const text = "Alpha unconfirmed navigation request";
  await walk.open(`${service}/projects/alpha`);
  await v.field("alpha").fill("Alpha running request");
  await v.send.click();
  await expect(v.text("Alpha partial reply.")).toBeVisible();
  const saved = deferred();
  const gate = deferred();
  const failed = deferred();
  let submissions = 0;
  await page.route((url) => url.pathname === "/api/chat", async (route) => {
    submissions++;
    expect((await route.fetch()).ok()).toBe(true);
    saved.release();
    await gate.promise;
    await route.abort("connectionfailed");
    failed.release();
  });
  await v.field("alpha").fill(text);
  await v.queue.click();
  await saved.promise;
  await switchProject(page, info, "beta");
  await v.field("beta").fill("Beta keeps its draft");
  gate.release();
  await failed.promise;
  await expect(v.field("beta")).toHaveValue("Beta keeps its draft");
  await walk.state("01-lost-receipt-destination-untouched", { visible: [v.field("beta")], hidden: [v.retry, v.convo.getByRole("alert")] });
  await switchProject(page, info, "alpha");
  await expect(v.field("alpha")).toHaveValue(text);
  const hint = v.convo.getByRole("alert").filter({ hasText: "Could not confirm delivery." });
  await walk.state("02-original-submitted-text-recovered", { visible: [hint, v.queued.getByText(text, { exact: true })], hidden: [v.retry] });
  await page.reload();
  await expect(v.field("alpha")).toHaveValue(text);
  await walk.state("03-reload-preserves-uncertainty", { visible: [hint, v.queued.getByText(text, { exact: true })], hidden: [v.retry] });
  const view = await (await request.get(`${service}/api/chat/alpha`)).json();
  expect(view.queued.filter((row: { text: string }) => row.text === text)).toHaveLength(1);
  expect((await (await request.get(`${service}/api/chat/beta`)).json()).history).toEqual([]);
  expect(submissions).toBe(1);
  expect((await request.post(`${service}/fixture/release/alpha`)).ok()).toBe(true);
  await expect.poll(async () => (await (await request.get(`${service}/api/chat/alpha`)).json()).history.some((row: { text: string }) => row.text === `${text} answered.`)).toBe(true);
  await expect.poll(async () => (await (await request.get(`${service}/api/chat/alpha`)).json()).busy).toBe(false);
  const calls = (await (await request.get(`${service}/fixture/calls`)).json()).calls;
  expect(calls.map((row: { text: string }) => row.text)).toEqual(["Alpha running request", text]);
});
