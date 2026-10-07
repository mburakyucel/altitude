import { expect } from "@playwright/test";
import { test } from "./fixtures";

// Use a fresh context without the fixture's route interception: warm means an actual HTTP cache.
// All timings are simulated-link observations, never physical-device acceptance or timing gates.
test.use({ serviceScript: "live-loading-service.py" });
const SLOW_4G = { offline: false, latency: 150, downloadThroughput: 1.6e6 / 8, uploadThroughput: 750e3 / 8 };
type Load = { url: string; encoded: number; finished: boolean; cached: boolean; encoding?: string };

for (const count of [100, 5000]) {
  test(`live loading measurement with ${count} source records`, { tag: "@chromium" }, async ({ browser, altitude, request }, info) => {
    test.setTimeout(180_000);
    const context = await browser.newContext({ ...info.project.use, baseURL: altitude.url });
    await context.addCookies([{ name: "altitude_device", value: altitude.device, url: altitude.url }]);
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", SLOW_4G);
    let loads = new Map<string, Load>();
    const cached = new Set<string>();
    cdp.on("Network.requestServedFromCache", ({ requestId }) => cached.add(requestId));
    cdp.on("Network.responseReceived", ({ requestId, response }) => loads.set(requestId, {
      url: new URL(response.url).pathname + new URL(response.url).search, encoded: 0, finished: false,
      cached: cached.has(requestId) || Boolean(response.fromDiskCache || response.fromMemoryCache),
      encoding: Object.entries(response.headers).find(([name]) => name.toLowerCase() === "content-encoding")?.[1] as string | undefined,
    }));
    cdp.on("Network.loadingFinished", ({ requestId, encodedDataLength }) => {
      const load = loads.get(requestId);
      if (load) Object.assign(load, { encoded: encodedDataLength, finished: true });
    });
    const measurements: unknown[] = [];
    const reset = () => { loads = new Map(); cached.clear(); return Date.now(); };
    const capture = async (name: string, started: number, extra = {}) => {
      // A visible row can paint just before CDP delivers loadingFinished; observe completed transfer.
      await expect.poll(() => [...loads.values()].filter((row) => row.url.includes("/transcript")).every((row) => row.finished)).toBe(true);
      const rows = [...loads.values()];
      const transcript = rows.filter((row) => row.url.includes("/transcript"));
      const measurement = { name, count, viewport: info.project.name, elapsedMs: Date.now() - started,
        transferredBytes: rows.reduce((sum, row) => sum + row.encoded, 0),
        transcriptBytes: transcript.reduce((sum, row) => sum + row.encoded, 0),
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
        const liveShownMs = Date.now() - started;
        await capture(`${temperature}-open`, started, { conversationShownMs, liveShownMs });
      }
      for (let index = 1; index <= 3; index += 1) {
        const started = reset();
        const update = await (await request.post("/fixture/append", { data: { slug: task.slug } })).json();
        await expect(live.getByText(update.latest, { exact: false }).first()).toBeVisible({ timeout: 60_000 });
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
