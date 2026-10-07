import { expect, type Locator, type Page, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "live-loading-service.py", single: true });

const transcriptMode = (url: URL, mode: string) => url.pathname.startsWith("/api/transcript/") &&
  (url.searchParams.get("mode") || "initial") === mode;

async function fixture(request: APIRequestContext, count = 100) {
  const status = await (await request.get("/fixture/status")).json();
  const task = status.tasks.find((row: { count: number }) => row.count === count);
  const control = async (mode: string) => {
    const response = await request.post("/fixture/control", { data: { slug: task.slug, mode } });
    expect(response.ok()).toBe(true);
    return response.json();
  };
  return { ...task, path: `/projects/atlas/tasks/${task.slug}/live`, control };
}

async function readingAnchor(body: Locator) {
  return body.evaluate((node) => {
    const top = node.getBoundingClientRect().top;
    const row = [...node.querySelectorAll<HTMLElement>("[data-transcript-id]")]
      .find((item) => item.getBoundingClientRect().bottom > top + 2);
    if (!row) throw new Error("No transcript row intersects the reading viewport");
    return { id: row.dataset.transcriptId!, offset: row.getBoundingClientRect().top - top };
  });
}

async function expectAnchor(body: Locator, anchor: Awaited<ReturnType<typeof readingAnchor>>) {
  const row = body.locator(`[data-transcript-id="${anchor.id}"]`);
  await expect(row).toHaveCount(1);
  await expect.poll(async () => row.evaluate((node, offset) => {
    const parent = node.closest(".live-body")!;
    return Math.abs(node.getBoundingClientRect().top - parent.getBoundingClientRect().top - offset);
  }, anchor.offset)).toBeLessThanOrEqual(2);
}

async function upward(page: Page, body: Locator) {
  await body.evaluate((node) => { node.scrollTop = 0; });
  if (page.context().browser()?.browserType().name() === "webkit") {
    // Mobile WebKit has no mouse-wheel API; exercise the viewer's keyboard intent path.
    await body.press("ArrowUp");
  } else {
    await body.hover();
    await page.mouse.wheel(0, -400);
  }
}

test("following after an epoch reset opens a bounded recent tail across a burst, with older history still accessible", async ({ page, request }, info) => {
  const task = await fixture(request);
  const walk = walkthrough(page, info);
  const live = page.getByRole("region", { name: "Live session", exact: true });
  await page.goto(task.path);
  await expect(live.getByText(task.latest, { exact: false })).toBeVisible();
  await expect(live.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  // Hold only transport while source mutation and index expiry are arranged. The application
  // then receives the real reset response and chooses the real follow/reconcile path itself.
  await page.route((url) => transcriptMode(url, "delta"), async (route) => {
    await gate;
    return route.continue();
  });
  const modes: string[] = [];
  page.on("request", (outgoing) => {
    const url = new URL(outgoing.url());
    if (url.pathname.startsWith("/api/transcript/")) modes.push(url.searchParams.get("mode") || "initial");
  });
  try {
    const burst = await task.control("burst");
    expect(burst.appended).toBe(220);
    await task.control("reset-index");
    const refreshed = page.waitForResponse((response) => transcriptMode(new URL(response.url()), "initial"));
    release();
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    const response = await refreshed;
    const bytes = await response.body();
    const tail = JSON.parse(bytes.toString());
    expect(bytes.byteLength).toBeLessThanOrEqual(65_536);
    expect(tail.events.length).toBeLessThanOrEqual(50);
    expect(tail.has_earlier).toBe(true);
    await expect(live.getByText(burst.latest, { exact: false })).toBeInViewport();
    await expect(live.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
    expect(modes.filter((mode) => mode === "initial")).toHaveLength(1);
    expect(modes, "A following reader does not replay the entire intervening gap").not.toContain("reconcile");
    expect(await live.locator("[data-transcript-id]").count()).toBeLessThanOrEqual(50);
    await walk.state("gap-01-following-recent-burst-tail", { visible: [live.getByText(burst.latest, { exact: false })], hidden: [live.getByText("Reconnecting to the session…", { exact: true })] });
    const older = page.waitForResponse((reply) => transcriptMode(new URL(reply.url()), "history"));
    const before = await live.locator("[data-transcript-id]").count();
    await upward(page, live.locator(".live-body"));
    await older;
    await expect.poll(() => live.locator("[data-transcript-id]").count()).toBeGreaterThan(before);
    const ids = await live.locator("[data-transcript-id]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-transcript-id")));
    expect(new Set(ids).size).toBe(ids.length);
    await walk.state("gap-02-earlier-burst-history-remains-accessible", { visible: [live.getByRole("button", { name: "Follow", exact: true })], hidden: [] });
  } finally {
    release();
  }
});

test("shrinking bottom content while following does not manufacture an upward gesture", async ({ page, request }, info) => {
  const task = await fixture(request);
  await task.control("long-record");
  const walk = walkthrough(page, info);
  const live = page.getByRole("region", { name: "Live session", exact: true });
  const body = live.locator(".live-body");
  await page.goto(task.path);
  await expect(live.getByText("Long fixture record", { exact: false }).first()).toBeInViewport();
  await expect(live.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  const before = await body.evaluate((node) => ({ height: node.scrollHeight, top: node.scrollTop }));
  await walk.state("shrink-01-following-long-tail", { visible: [live.getByRole("button", { name: "Pause", exact: true })], hidden: [live.getByRole("button", { name: "Follow", exact: true })] });
  // Rewrite actual source text; no DOM geometry, scrolling or application state is faked.
  await task.control("short-tail");
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(live.getByText("Shortened fixture activity.", { exact: true })).toBeInViewport();
  await expect.poll(() => body.evaluate((node) => node.scrollHeight)).toBeLessThan(before.height - 300);
  await expect.poll(() => body.evaluate((node) => node.scrollTop)).toBeLessThan(before.top - 300);
  await expect(live.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  await expect(live.getByRole("button", { name: "Follow", exact: true })).toHaveCount(0);
  await expect.poll(() => body.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThanOrEqual(2);
  const update = await task.control("append");
  await expect(live.getByText(update.latest, { exact: false })).toBeInViewport();
  await walk.state("shrink-02-short-tail-and-next-output-still-follow", { visible: [live.getByRole("button", { name: "Pause", exact: true }), live.getByText(update.latest, { exact: false })], hidden: [live.getByRole("button", { name: "Follow", exact: true })] });
});

test("Pause during a pending tail refresh preserves the visible reading window", async ({ page, request }, info) => {
  const task = await fixture(request);
  const live = page.getByRole("region", { name: "Live session", exact: true });
  const body = live.locator(".live-body");
  await page.goto(task.path);
  await expect(live.getByText(task.latest, { exact: false })).toBeInViewport();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const pending = page.waitForRequest((outgoing) => transcriptMode(new URL(outgoing.url()), "initial"));
  await page.route((url) => transcriptMode(url, "initial"), async (route) => { await gate; return route.continue(); });
  try {
    const burst = await task.control("burst");
    await task.control("reset-index");
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await pending;
    await live.getByRole("button", { name: "Pause", exact: true }).click();
    const anchor = await readingAnchor(body);
    const reconciled = page.waitForResponse((reply) => transcriptMode(new URL(reply.url()), "reconcile"));
    release();
    await reconciled;
    await expect(live.getByText(burst.latest, { exact: false })).toBeAttached();
    await expect(live.getByText("Reconnecting to the session…", { exact: true })).toHaveCount(0);
    await expectAnchor(body, anchor);
    await expect(live.getByText(task.latest, { exact: false })).toBeInViewport();
    await expect(live.getByText(burst.latest, { exact: false })).not.toBeInViewport();
    await walkthrough(page, info).state("pause-race-retains-reading-window", {
      visible: [live.getByRole("button", { name: "Follow", exact: true }), live.getByText(task.latest, { exact: false })], hidden: [],
    });
  } finally { release(); }
});

test("infinite history loads on upward intent, preserves expansion and anchor through failure, and reaches complete history", async ({ page, request }, info) => {
  const task = await fixture(request);
  const walk = walkthrough(page, info);
  const live = page.getByRole("region", { name: "Live session", exact: true });
  const body = live.locator(".live-body");
  await page.goto(task.path);
  await expect(live.getByText(task.latest, { exact: false })).toBeVisible();
  const initialIds = await live.locator("[data-transcript-id]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-transcript-id")));
  expect(initialIds.length).toBeLessThanOrEqual(50);
  await expect(live.getByText("Activity 100 0:", { exact: false })).toHaveCount(0);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let first = true;
  let historyRequests = 0;
  await page.route((url) => transcriptMode(url, "history"), async (route) => {
    historyRequests += 1;
    if (!first) return route.continue();
    first = false;
    await gate;
    return route.fulfill({ status: 503, contentType: "application/json", body: '{"error":"Fictional history transport outage"}' });
  });
  try {
    // Install the loading gate before an expansion or scroll can request history.
    const call = live.locator("details.session-tool").first();
    await call.locator("summary").click();
    const callId = await call.evaluate((node) => node.closest<HTMLElement>("[data-transcript-id]")!.dataset.transcriptId);
    await upward(page, body);
    await walk.state("history-01-loading-keeps-content", {
      visible: [live.getByText("Loading earlier activity…", { exact: true })], hidden: [],
    });
    await expect(live.getByText("Loading earlier activity…", { exact: true })).toBeInViewport();
    const anchor = await readingAnchor(body);
    expect(await live.locator("[data-transcript-id]").count()).toBe(initialIds.length);
    release();
    await walk.state("history-02-error-keeps-content", {
      visible: [live.getByText("Could not load earlier activity.", { exact: false }), live.getByRole("button", { name: "Retry", exact: true })],
      hidden: [live.getByText("Loading earlier activity…", { exact: true })],
    });
    await expect(live.getByText("Could not load earlier activity.", { exact: false })).toBeInViewport();
    await expectAnchor(body, anchor);
    await live.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(live.getByText("Beginning of session", { exact: true })).toBeAttached();
    await expectAnchor(body, anchor);
    await expect(live.locator(`[data-transcript-id="${callId}"] details`)).toHaveAttribute("open", "");
    const ids = await live.locator("[data-transcript-id]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-transcript-id")));
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of initialIds) expect(ids).toContain(id);
    for (let index = 0; index <= 100; index += 4) {
      await expect(live.getByText(`Activity 100 ${index}:`, { exact: false })).toHaveCount(1);
    }
    for (let index = 0; index < 25; index += 1) {
      await expect(live.getByText(`Inspect fixture ${index}`, { exact: true })).toHaveCount(1);
    }
    expect(historyRequests, "One upward gesture plus explicit Retry, with no eager history cascade").toBe(2);
    await walk.state("history-03-complete-continuous-history", { visible: [live.getByRole("button", { name: "Follow", exact: true })], hidden: [] });

    await task.control("late");
    await expect(live.getByText("Activity late -4:", { exact: false })).toHaveCount(1);
    await expectAnchor(body, anchor);
    await task.control("command-result");
    const oldCall = live.locator("details.session-tool").filter({ hasText: "Inspect fixture 0" });
    await oldCall.locator("summary").click();
    await expect(oldCall.getByText("Late fixture command result: complete.", { exact: false })).toBeVisible();
    await walk.state("history-04-old-tool-receives-late-result", { visible: [oldCall.getByText("Late fixture command result: complete.", { exact: false })], hidden: [] });
  } finally {
    release();
  }
});

test("raw history has unique records and explicit chunked disclosure with local loading, error, retry and cancel", async ({ page, request }, info) => {
  const task = await fixture(request);
  await task.control("long-record");
  const walk = walkthrough(page, info);
  const live = page.getByRole("region", { name: "Live session", exact: true });
  await page.goto(task.path);
  await expect(live.getByText("Long fixture record", { exact: false }).first()).toBeVisible();
  await live.getByRole("button", { name: "Raw events", exact: true }).click();
  const raw = live.getByRole("region", { name: "Raw events", exact: true });
  const row = raw.locator(".raw-row").filter({ hasText: "Long fixture record" });
  await expect(row).toHaveCount(1);
  await expect(row.getByRole("button", { name: "Full record", exact: true })).toBeVisible();
  let release!: () => void;
  let gate = new Promise<void>((resolve) => { release = resolve; });
  let state = "fail";
  const chunks: string[] = [];
  const nextChunk = () => page.waitForResponse((response) => transcriptMode(new URL(response.url()), "record") && response.ok());
  await page.route((url) => transcriptMode(url, "record"), async (route) => {
    if (state !== "ready") await gate;
    return state === "fail"
      ? route.fulfill({ status: 503, contentType: "application/json", body: '{"error":"Fictional record transport outage"}' })
      : route.continue();
  });
  try {
    await row.getByRole("button", { name: "Full record", exact: true }).click();
    await walk.state("raw-01-local-record-loading", { visible: [row.getByText("Loading record…", { exact: false }), row.getByRole("button", { name: "Cancel", exact: true })], hidden: [] });
    release();
    await walk.state("raw-02-local-record-error", { visible: [row.getByText("Could not read this record.", { exact: false }), row.getByRole("button", { name: "Retry", exact: true })], hidden: [] });
    state = "ready";
    let chunk = nextChunk();
    await row.getByRole("button", { name: "Retry", exact: true }).click();
    chunks.push((await (await chunk).json()).text);
    await expect(row.getByRole("button", { name: "Show more", exact: true })).toBeVisible();
    // Cancel a later chunk while retaining the first one; cancellation need not close disclosure.
    state = "hold";
    gate = new Promise<void>((resolve) => { release = resolve; });
    await row.getByRole("button", { name: "Show more", exact: true }).click();
    await expect(row.getByText("Loading record…", { exact: false })).toBeVisible();
    await row.getByRole("button", { name: "Cancel", exact: true }).click();
    state = "ready";
    release();
    await walk.state("raw-03-canceled-chunk-keeps-detail", { visible: [row.getByRole("button", { name: "Show more", exact: true })], hidden: [row.getByText("Loading record…", { exact: false })] });
    await expect(row.locator("div > pre.session-out")).toHaveText(chunks[0]!);
    for (let index = 0; index < 8 && await row.getByRole("button", { name: "Show more", exact: true }).count(); index += 1) {
      chunk = nextChunk();
      await row.getByRole("button", { name: "Show more", exact: true }).click();
      chunks.push((await (await chunk).json()).text);
      await expect(row.getByText("Loading record…", { exact: false })).toHaveCount(0);
    }
    await expect(row.getByRole("button", { name: "Show more", exact: true })).toHaveCount(0);
    expect(chunks.length).toBeGreaterThan(1);
    for (const text of chunks) expect(text.length).toBeLessThanOrEqual(4000);
    expect(JSON.parse(chunks.join("")).row.text).toBe("Long fixture record " + "readable detail ".repeat(900));
    await walk.state("raw-04-full-redacted-record", { visible: [row.getByRole("button", { name: "Hide full record", exact: true })], hidden: [row.getByRole("button", { name: "Show more", exact: true })] });
    await row.getByRole("button", { name: "Hide full record", exact: true }).click();
    const body = live.locator(".live-body");
    for (let index = 0; index < 4 && !await live.getByText("Beginning of session", { exact: true }).count(); index += 1) {
      const before = await raw.locator("[data-transcript-id]").count();
      const loaded = page.waitForResponse((response) => transcriptMode(new URL(response.url()), "history"));
      await upward(page, body);
      await loaded;
      await expect.poll(() => raw.locator("[data-transcript-id]").count()).toBeGreaterThan(before);
      await expect(live.getByText("Loading earlier activity…", { exact: true })).toHaveCount(0);
    }
    await expect(live.getByText("Beginning of session", { exact: true })).toBeAttached();
    const ids = await raw.locator("[data-transcript-id]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-transcript-id")));
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.length).toBeGreaterThanOrEqual(102);
  } finally {
    state = "ready";
    release();
  }
});

test("switching away from a pending initial read aborts it and cannot populate another task", async ({ page, request }, info) => {
  const task = await fixture(request);
  const empty = await fixture(request, 0);
  const walk = walkthrough(page, info);
  const live = page.getByRole("region", { name: "Live session", exact: true });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route((url) => transcriptMode(url, "initial") && url.pathname.endsWith(`/${task.slug}`), async (route) => {
    await gate;
    return route.continue();
  });
  const canceled = page.waitForEvent("requestfailed", { predicate: (outgoing) => {
    const url = new URL(outgoing.url());
    return transcriptMode(url, "initial") && url.pathname.endsWith(`/${task.slug}`);
  } });
  try {
    await page.goto(task.path);
    await expect(live.getByText("Connecting to the session…", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Back", exact: true }).click();
    if (info.project.name === "phone") await page.getByRole("link", { name: "Work", exact: true }).click();
    await page.locator(`a[href="/projects/atlas/tasks/${empty.slug}"]`).first().click();
    if (info.project.name === "phone") await page.getByRole("link", { name: "Live session", exact: true }).click();
    release();
    await canceled;
    await walk.state("navigation-01-other-task-empty", { visible: [live.getByText("Connecting to the session…", { exact: true })], hidden: [live.getByText(task.latest, { exact: false })] });
    await expect(live.locator("[data-transcript-id]").filter({ hasText: "Activity 100" })).toHaveCount(0);
  } finally {
    release();
  }
});

test("live transport error and server index reset reconcile an older reader without losing expansion", async ({ page, request }, info) => {
  const task = await fixture(request);
  const walk = walkthrough(page, info);
  const live = page.getByRole("region", { name: "Live session", exact: true });
  const body = live.locator(".live-body");
  await page.goto(task.path);
  await expect(live.getByText(task.latest, { exact: false })).toBeVisible();
  await upward(page, body);
  await expect(live.getByText("Beginning of session", { exact: true })).toBeAttached();
  const call = live.locator("details.session-tool").first();
  await call.locator("summary").click();
  await body.evaluate((node) => { node.scrollTop = 300; });
  const anchor = await readingAnchor(body);
  const callId = await call.evaluate((node) => node.closest<HTMLElement>("[data-transcript-id]")!.dataset.transcriptId);
  let fail = true;
  await page.route((url) => transcriptMode(url, "delta"), (route) => fail
    ? route.fulfill({ status: 503, contentType: "application/json", body: '{"error":"Fictional live transport outage"}' })
    : route.continue());
  await expect(live.getByText("Could not update the session.", { exact: false })).toBeAttached({ timeout: 10_000 });
  await expectAnchor(body, anchor);
  await walk.state("reconnect-01-retained-history-disconnected", { visible: [live.getByRole("button", { name: "Follow", exact: true })], hidden: [] });
  fail = false;
  await task.control("reset-index");
  const reconciled = page.waitForResponse((response) => transcriptMode(new URL(response.url()), "reconcile"));
  // Reconnection itself must preserve an older reader. Clicking the offscreen footer would
  // deliberately scroll that reader to the Retry control before the request even starts.
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await reconciled;
  await expect(live.getByText("Could not update the session.", { exact: false })).toHaveCount(0);
  await expect(live.getByText("Reconnecting to the session…", { exact: true })).toHaveCount(0);
  await expectAnchor(body, anchor);
  await expect(live.locator(`[data-transcript-id="${callId}"] details`)).toHaveAttribute("open", "");
  await expect(live.getByText("Activity 100 0:", { exact: false })).toHaveCount(1);
  await walk.state("reconnect-02-anchor-restored", { visible: [live.getByRole("button", { name: "Follow", exact: true })], hidden: [] });
});

test("initial loading, read error, empty activity and denied access have explicit states", async ({ page, request }, info) => {
  const task = await fixture(request);
  const walk = walkthrough(page, info);
  const live = page.getByRole("region", { name: "Live session", exact: true });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let state = "hold";
  await page.route((url) => transcriptMode(url, "initial"), async (route) => {
    if (state === "hold") {
      await gate;
      return route.fulfill({ status: 503, contentType: "application/json", body: '{"error":"Fictional initial transport outage"}' });
    }
    if (state === "denied") return route.fulfill({ status: 403, contentType: "application/json", body: '{"error":"Fictional access refusal"}' });
    return route.continue();
  });
  try {
    await page.goto(task.path);
    await walk.state("initial-01-loading", { visible: [live.getByText("Connecting to the session…", { exact: true })], hidden: [] });
    release();
    await walk.state("initial-02-read-error", { visible: [live.getByText("Could not read the session.", { exact: false }), live.getByRole("button", { name: "Retry", exact: true })], hidden: [] });
    state = "ready";
    await live.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(live.getByText(task.latest, { exact: false })).toBeVisible();
    const empty = await fixture(request, 0);
    await page.goto(empty.path);
    await walk.state("initial-03-empty-running-session", { visible: [live.getByText("Connecting to the session…", { exact: true })], hidden: [live.getByText(task.latest, { exact: false })] });
    state = "denied";
    await page.goto(task.path);
    await expect(live.getByText(task.latest, { exact: false })).toHaveCount(0);
    await walk.state("initial-04-denied-clears-history", { visible: [live.getByText(/Could not read the session|No session file|Access/).first()], hidden: [live.getByText(task.latest, { exact: false })] });
  } finally {
    release();
  }
});
