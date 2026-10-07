import { expect } from "@playwright/test";
import { test } from "./fixtures";

// Use a fresh context without the fixture's route interception: warm means an actual HTTP cache.
// All timings are simulated-link observations, never physical-device acceptance or timing gates.
test.use({ serviceScript: "live-loading-service.py" });
const SLOW_4G = { offline: false, latency: 150, downloadThroughput: 1.6e6 / 8, uploadThroughput: 750e3 / 8 };
type Load = { requestId: string; url: string; encoded: number; finished: boolean; cached: boolean;
  failed: boolean; canceled: boolean; error?: string; encoding?: string };

for (const count of [100, 5000]) {
  test(`live loading measurement with ${count} source records`, { tag: "@chromium" }, async ({ browser, altitude, request }, info) => {
    test.setTimeout(180_000);
    const context = await browser.newContext({ ...info.project.use, baseURL: altitude.url });
    await context.addCookies([{ name: "altitude_device", value: altitude.device, url: altitude.url }]);
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", SLOW_4G);
    const requests = new Map<string, Load>();
    let phase: Load[] = [];
    cdp.on("Network.requestWillBeSent", ({ requestId, request: outgoing }) => {
      const url = new URL(outgoing.url);
      const load: Load = { requestId, url: url.pathname + url.search, encoded: 0,
        finished: false, cached: false, failed: false, canceled: false };
      requests.set(requestId, load);
      phase.push(load);
    });
    cdp.on("Network.requestServedFromCache", ({ requestId }) => {
      const load = requests.get(requestId);
      if (load) load.cached = true;
    });
    cdp.on("Network.responseReceived", ({ requestId, response }) => {
      const load = requests.get(requestId);
      if (load) Object.assign(load, {
        encoded: response.encodedDataLength,
        cached: load.cached || Boolean(response.fromDiskCache || response.fromMemoryCache),
        encoding: Object.entries(response.headers).find(([name]) => name.toLowerCase() === "content-encoding")?.[1] as string | undefined,
      });
    });
    cdp.on("Network.dataReceived", ({ requestId, encodedDataLength }) => {
      const load = requests.get(requestId);
      if (load) load.encoded += encodedDataLength;
    });
    cdp.on("Network.loadingFinished", ({ requestId, encodedDataLength }) => {
      const load = requests.get(requestId);
      if (load) Object.assign(load, { encoded: encodedDataLength, finished: true });
    });
    cdp.on("Network.loadingFailed", ({ requestId, canceled, errorText }) => {
      const load = requests.get(requestId);
      if (load) Object.assign(load, { failed: true, canceled: Boolean(canceled), error: errorText });
    });
    const measurements: unknown[] = [];
    // Attribute each request to the phase where it starts, including failures before response headers.
    const reset = () => { phase = []; return Date.now(); };
    const capture = async (name: string, started: number, extra = {}) => {
      const elapsedMs = Date.now() - started;
      // Freeze a finite request set at the landmark. Later polls cannot extend this wait, and an
      // aborted navigation request is terminal too. Keep user-visible timing separate from settling.
      const captured = [...phase];
      const capturedTranscript = captured.filter((row) => row.url.includes("/transcript"));
      await expect.poll(() => capturedTranscript.every((row) => row.finished || row.failed), { timeout: 60_000 }).toBe(true);
      const rows = captured.map((row) => ({ ...row }));
      const transcript = rows.filter((row) => row.url.includes("/transcript"));
      const measurement = { name, count, viewport: info.project.name, elapsedMs,
        transferSettledMs: Date.now() - started,
        transferredBytes: rows.reduce((sum, row) => sum + row.encoded, 0),
        completedBytes: rows.filter((row) => row.finished).reduce((sum, row) => sum + row.encoded, 0),
        // Failed/in-flight transfers expose only the bytes CDP has reported so far, a lower bound.
        partialBytes: rows.filter((row) => !row.finished).reduce((sum, row) => sum + row.encoded, 0),
        transcriptBytes: transcript.reduce((sum, row) => sum + row.encoded, 0),
        largestTranscriptBytes: Math.max(0, ...transcript.map((row) => row.encoded)),
        canceledRequests: rows.filter((row) => row.canceled).length,
        failedRequests: rows.filter((row) => row.failed && !row.canceled).length,
        transcriptRequests: transcript.length, loads: rows, ...extra };
      measurements.push(measurement);
      await info.attach(`${name}-${count}.json`, { contentType: "application/json", body: JSON.stringify(measurement, null, 2) });
    };
    try {
      const fixture = await (await request.get("/fixture/status")).json();
      const task = fixture.tasks.find((row: { count: number }) => row.count === count);
      const other = fixture.tasks.find((row: { count: number }) => row.count !== count);
      const path = `/projects/atlas/tasks/${task.slug}`;
      const live = page.getByRole("region", { name: "Live session", exact: true });
      const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
      const toLive = async () => {
        if (info.project.name === "phone") await page.getByRole("link", { name: "Live session", exact: true }).click();
      };
      for (const temperature of ["cold", "warm"]) {
        const started = reset();
        await page.goto(path);
        await expect(conversation.getByText(`Conversation for ${count} records is ready.`)).toBeVisible();
        const conversationShownMs = Date.now() - started;
        await toLive();
        await expect(live.getByText(task.latest, { exact: false }).first()).toBeVisible({ timeout: 60_000 });
        await expect(live.getByText(task.latest, { exact: false }).first()).toBeInViewport();
        const liveShownMs = Date.now() - started;
        await capture(`${temperature}-open`, started, { conversationShownMs, liveShownMs });
      }
      for (let index = 1; index <= 3; index += 1) {
        const started = reset();
        const update = await (await request.post("/fixture/append", { data: { slug: task.slug } })).json();
        await expect(live.getByText(update.latest, { exact: false }).first()).toBeVisible({ timeout: 60_000 });
        await expect(live.getByText(update.latest, { exact: false }).first()).toBeInViewport();
        await capture(`update-${index}`, started);
      }
      let started = reset();
      await live.getByRole("button", { name: "Raw events", exact: true }).click();
      await expect(live.getByRole("region", { name: "Raw events", exact: true }).getByText(task.latest, { exact: false }).first()).toBeAttached({ timeout: 60_000 });
      await capture("raw-open", started);
      started = reset();
      // Route links keep the application alive; goto would measure a document reload instead.
      await page.getByRole("button", { name: "Back", exact: true }).click();
      if (info.project.name === "phone") await page.getByRole("link", { name: "Chat", exact: true }).click();
      await expect(page.getByRole("textbox", { name: "Message L3 about atlas" })).toBeVisible();
      await capture("project-navigation", started);
      started = reset();
      if (info.project.name === "phone") await page.getByRole("link", { name: "Work", exact: true }).click();
      await page.locator(`a[href="/projects/atlas/tasks/${other.slug}"]`).first().click();
      await expect(conversation.getByText(`Conversation for ${other.count} records is ready.`)).toBeVisible();
      const conversationShownMs = Date.now() - started;
      await toLive();
      await expect(live.getByText(other.latest, { exact: false }).first()).toBeVisible({ timeout: 60_000 });
      await capture("task-switch", started, { conversationShownMs });
      await page.screenshot({ path: info.outputPath(`live-loading-${count}-${info.project.name}.png`) });
    } finally {
      await info.attach(`measurements-${count}.json`, { contentType: "application/json", body: JSON.stringify({ link: SLOW_4G, measurements }, null, 2) });
      await context.close();
    }
  });
}
