import { test } from "./fixtures";
import { expect, type Browser, type TestInfo } from "@playwright/test";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/*
 * Opening Altitude (docs/ARCHITECTURE.md#web-delivery), walked at 390 and 1440 in a context of its own:
 * the shared fixture context keeps request interception on, which turns Chromium's HTTP cache off, and
 * a warm open is only a warm open with the cache in place. Each open records its requests and the bytes
 * that crossed the network; the timings are attached, and the assertions stay on bytes and requests.
 */

const DICTATION = /\/assets\/(model|vocab|ort-wasm-simd|worker)-[^/]+$/;
const SCRIPT = /\/assets\/index-[^/]+\.js$/;
const SLOW_4G = { offline: false, latency: 150, downloadThroughput: 1.6e6 / 8, uploadThroughput: 750e3 / 8 };

type Load = { url: string; encoded: number; finished: boolean; cached: boolean; encoding?: string };

/** A page in its own context, closed before the disposable service stops, also after a failed step. */
const firstOpen = test.extend<{ visit: Awaited<ReturnType<typeof opener>> }>({
  visit: async ({ browser, altitude, service }, use, info) => {
    const visit = await opener(browser, info, service, altitude.device);
    await use(visit);
    await visit.page.context().close();
  },
});

async function opener(browser: Browser, info: TestInfo, service: string, device: string) {
  const context = await browser.newContext({ ...info.project.use, baseURL: service });
  await context.addCookies([{ name: "altitude_device", value: device, url: service }]);
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send("Network.enable");
  let loads = new Map<string, Load>();
  let fromCache = new Set<string>();
  cdp.on("Network.requestServedFromCache", ({ requestId }) => fromCache.add(requestId));
  cdp.on("Network.responseReceived", ({ requestId, response }) => loads.set(requestId, {
    url: new URL(response.url).pathname, encoded: 0, finished: false,
    cached: fromCache.has(requestId) || Boolean(response.fromDiskCache || response.fromMemoryCache),
    encoding: Object.entries(response.headers).find(([name]) => name.toLowerCase() === "content-encoding")?.[1] as string | undefined,
  }));
  cdp.on("Network.loadingFinished", ({ requestId, encodedDataLength }) => {
    const load = loads.get(requestId);
    if (load) Object.assign(load, { encoded: encodedDataLength, finished: true });
  });
  return {
    page,
    throttle: () => cdp.send("Network.emulateNetworkConditions", SLOW_4G),
    async open(path: string, name: string) {
      loads = new Map();
      fromCache = new Set();
      const started = Date.now();
      await page.goto(path);
      await expect(page.getByRole("main")).toBeVisible();
      const shown = Date.now() - started;
      // The change stream stays open, so the page never goes network-idle: the script it ran has finished.
      await expect.poll(() => [...loads.values()].find((load) => SCRIPT.test(load.url))?.finished).toBe(true);
      const all = [...loads.values()];
      await info.attach(`${name}-requests.json`, { contentType: "application/json", body: JSON.stringify({ shownMs: shown, loads: all }, null, 2) });
      return all;
    },
  };
}

const field = (main: import("@playwright/test").Locator) => main.getByRole("textbox", { name: /^Message (?:L3 about |the L2$)/, includeHidden: true });

firstOpen("first open: the app arrives compressed, opening fetches nothing for dictation, and a warm open reuses it", { tag: "@chromium" }, async ({ visit, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(visit.page, info);
  const main = visit.page.getByRole("main");

  const cold = await visit.open(project.path, "first-open-cold");
  const script = cold.find((load) => SCRIPT.test(load.url));
  expect(script?.encoding, "The app script travels gzip-encoded").toBe("gzip");
  expect(script!.cached).toBe(false);
  expect(cold.filter((load) => DICTATION.test(load.url)), "Opening waits for no dictation asset").toEqual([]);
  await walk.state("first-open-01-cold-typing", {
    action: () => field(main).fill("a draft typed on first open"),
    visible: [field(main), main.getByRole("button", { name: "Start voice input", exact: true })],
    hidden: [main.getByRole("status", { name: "Transcribing…", exact: true })],
  });

  const warm = await visit.open(project.path, "first-open-warm");
  expect(warm.find((load) => SCRIPT.test(load.url))?.cached, "A warm open takes the app script from the cache").toBe(true);
  expect(warm.filter((load) => DICTATION.test(load.url))).toEqual([]);
  await walk.state("first-open-02-warm", { visible: [field(main)], hidden: [] });
});

firstOpen("first open on a slow connection with dictation assets failing: the page opens and typing works", { tag: "@chromium" }, async ({ visit, request }, info) => {
  test.setTimeout(60_000);
  const project = await fixtureProject(request);
  const walk = walkthrough(visit.page, info);
  const main = visit.page.getByRole("main");
  let refused = 0;
  await visit.page.route((url) => DICTATION.test(url.pathname), (route) => { refused += 1; return route.abort(); });
  await visit.throttle();

  const cold = await visit.open(project.path, "first-open-slow");
  const script = cold.find((load) => SCRIPT.test(load.url))!;
  // Slow 4G moves about 200 kB a second: the bytes on the wire decide how long the page stays blank.
  expect(script.encoding).toBe("gzip");
  expect(script.encoded, "The compressed script is a third of the built one").toBeLessThan(300_000);
  await walk.state("first-open-03-slow-failing-dictation-typing", {
    action: () => field(main).fill("typed while dictation assets fail"),
    visible: [field(main), main.getByRole("button", { name: "Send", exact: true })],
    hidden: [main.locator(".composer-hint", { hasText: "without punctuation" })],
  });
  await expect(field(main)).toHaveValue("typed while dictation assets fail");
  expect(refused, "Nothing asked for a dictation asset before the microphone").toBe(0);
});
