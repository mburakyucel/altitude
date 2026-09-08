import { expect, type Page, type TestInfo } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ scenario: "isolation" });

test.setTimeout(60_000);

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

test("project selection drops drafts and loads only the destination history", async ({ page, service }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  await walk.open(`${service}/projects/alpha`);
  await v.field("alpha").fill("Alpha unsent draft");
  await walk.state("01-alpha-draft", { visible: [v.text("Alpha saved history."), v.field("alpha")], hidden: [v.loading] });
  const gate = deferred();
  await page.route((url) => url.pathname === "/api/chat/beta", async (route) => { await gate.promise; await route.continue(); }, { times: 1 });
  await switchProject(page, info, "beta");
  await walk.state("02-beta-loading-overlay", { visible: [v.loading, v.field("beta")], hidden: [v.text("Alpha saved history.")] });
  await expect(v.field("beta")).toHaveValue("");
  gate.release();
  await walk.state("03-beta-empty", { visible: [v.text("Say what you want done. L3 answers or creates one task.")], hidden: [v.loading, v.text("Alpha saved history.")] });
  await switchProject(page, info, "alpha");
  await expect(v.field("alpha")).toHaveValue("");
  await walk.state("04-alpha-saved-history-only", { visible: [v.text("Alpha saved history.")], hidden: [v.text("Alpha unsent draft")] });
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
    await expect(v.field("beta")).toHaveValue("");
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
  await expect(v.field("alpha")).toHaveValue("");
  await walk.state("02-alpha-no-unaccepted-draft", { visible: [v.text("Alpha saved history.")], hidden: [v.retry, v.text("Alpha refused request")] });
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

test("listening, late transcription and denied microphone state reset on selection", async ({ page, service }, info) => {
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
  const mic = v.convo.getByRole("button", { name: "Start voice input" });
  const stop = v.convo.getByRole("button", { name: "Stop voice input" });
  const wave = v.convo.locator(".composer-wave");
  await mic.click();
  await walk.state("01-alpha-listening-overlay", { visible: [stop, wave], hidden: [mic] });
  await switchProject(page, info, "beta");
  await walk.state("02-beta-listening-ui-reset-overlay", { visible: [mic, v.field("beta")], hidden: [stop, wave] });
  await switchProject(page, info, "alpha");
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
  await walk.state("03-alpha-transcribing-overlay", { visible: [v.text("Transcribing…"), wave], hidden: [stop] });
  await switchProject(page, info, "beta");
  await v.field("beta").fill("Beta typed during transcription");
  transcript.release();
  await delivered.promise;
  await expect(v.field("beta")).toHaveValue("Beta typed during transcription");
  await walk.state("04-beta-late-transcript-ignored-overlay", { visible: [mic, v.field("beta")], hidden: [wave, v.text("Transcribing…"), v.text("Alpha late transcript")] });
  await switchProject(page, info, "alpha");
  await page.evaluate("window.fixtureDenied = true");
  await mic.click();
  const denied = v.text("Microphone blocked in the browser. Typing works.");
  await walk.state("05-alpha-denied-overlay", { visible: [denied, mic], hidden: [wave, stop] });
  await expect(mic).toBeDisabled();
  await switchProject(page, info, "beta");
  await expect(mic).toBeEnabled();
  await expect(v.field("beta")).toHaveValue("");
  await walk.state("06-beta-microphone-state-reset-overlay", { visible: [mic, v.field("beta")], hidden: [denied, wave, stop] });
  await expect.poll(() => page.evaluate("window.fixtureStreams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))")).toBe(true);
});
