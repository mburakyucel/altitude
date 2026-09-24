import { test } from "./fixtures";
import { expect, type Page } from "@playwright/test";
import { walkthrough } from "./walkthrough";

/**
 * Monitor presentation states (SPEC.md §3.14) over the isolated service: HTTP overlays hold or fail a read
 * and show missing/stale readings, a single engine or no sessions. Real routing remains in the server.
 */
type Json = Record<string, unknown>;
type Seat = { engine: string; label: string; quota: Json };

/** The same rows the API sent, with one patch applied to every seat's reading. */
function seatsWith(body: Json, patch: (quota: Json) => Json): Seat[] {
  return ((body.seats ?? []) as Seat[]).map((seat) => ({ ...seat, quota: patch(seat.quota) }));
}

async function overlay(page: Page, path: string, patch: (body: Json) => Json) {
  const response = await page.request.get(path);
  expect(response.ok()).toBe(true);
  const body = patch((await response.json()) as Json);
  await page.route(`**${path}*`, (route) => route.fulfill({ json: body }));
}

const hoursAgo = (hours: number) => new Date(Date.now() - hours * 3600_000);

/** Slice 1 reported the phone Monitor clipping and sitting flush with the edge (SPEC.md §2.2). */
async function fitsInViewport(page: Page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  const layout = await page.evaluate(() => ({
    overflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - document.documentElement.clientWidth,
    sidePadding: parseFloat(getComputedStyle(document.querySelector(".page")!).paddingLeft),
    clipped: [...document.querySelectorAll(".monitor-route-why, .monitor-muted, .restart-banner p")]
      .filter((el) => el.scrollWidth > el.clientWidth + 1).length,
  }));
  expect(layout.overflow, "no viewport scrolls horizontally").toBeLessThanOrEqual(0);
  expect(layout.sidePadding, "the page has side padding").toBeGreaterThanOrEqual(16);
  expect(layout.clipped, "routing lines wrap instead of clipping").toBe(0);
}

test("Monitor walks loading, ready, error, retry, and the readings' states", async ({ page, request }, info) => {
  // Eight reloads and one read that fails three retries first: longer than Playwright's default 30s.
  test.setTimeout(120_000);
  const walk = walkthrough(page, info);
  const seats = (await (await request.get("/api/monitor")).json()).seats as Seat[];
  expect(seats.length, "the seam reports at least one configured engine").toBeGreaterThan(0);
  const loading = page.getByLabel("Loading", { exact: true });
  const routing = page.getByRole("heading", { name: "Routing now", exact: true });
  const seatsHead = page.getByRole("heading", { name: "Seats", exact: true });
  const sessions = page.getByRole("heading", { name: /^Sessions \(\d+\)$/ });
  // The sentence and its Retry share one paragraph: match the start, not the whole text.
  const error = page.getByText(/^Could not read the monitor\./);
  const retry = page.getByRole("button", { name: "Retry", exact: true });
  const usage = page.getByRole("button", { name: "L2 usage details", exact: true });

  // Loading: the real read, held until the skeleton is on screen.
  let release = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/monitor*", async (route) => { await held; await route.continue(); });
  await walk.open("/monitor");
  await walk.state("01-loading", { visible: [loading, page.getByRole("heading", { name: "Monitor", exact: true })], hidden: [routing, error, usage] });
  await walk.state("02-ready", { action: async () => { release(); }, visible: [seatsHead, routing, sessions], hidden: [loading, error] });
  await page.unroute("**/api/monitor*");
  for (const seat of seats) await expect(page.getByRole("region", { name: seat.label, exact: true })).toBeVisible();
  await fitsInViewport(page);

  // Error: every read fails until Retry is pressed; the app retries three times first.
  let failing = true;
  await page.route("**/api/monitor*", async (route) => {
    if (failing) await route.fulfill({ status: 500, json: { error: "monitor unavailable" } });
    else await route.continue();
  });
  await walk.open("/monitor");
  await error.waitFor({ timeout: 30_000 });
  await walk.state("03-error", { visible: [error, retry], hidden: [loading, routing, seatsHead, usage] });
  await walk.state("04-retry", {
    action: async () => { failing = false; await retry.click(); },
    visible: [seatsHead, routing, sessions], hidden: [error, retry, loading],
  });
  await page.unroute("**/api/monitor*");

  // No reading: no seat has ever been read.
  await overlay(page, "/api/monitor", (body) => ({
    ...body,
    seats: seatsWith(body, () => ({ known: false, why: "the seat has not been read yet" })),
  }));
  await walk.open("/monitor");
  await walk.state("05-no-reading-overlay", {
    visible: [page.getByText(/^No reading\./).first(), routing],
    hidden: [page.locator(".monitor-seat .monitor-age"), page.locator(".meter-reserve"), page.getByText("Stale", { exact: true })],
  });
  await page.unroute("**/api/monitor*");

  // Stale: the figures stay, dimmed, with their age and the label.
  await overlay(page, "/api/monitor", (body) => ({
    ...body,
    seats: seatsWith(body, (quota) => ({
      ...quota, known: false, stale: true,
      // The real source may never have emitted a figure. This state needs an actual old
      // observation, not just a stale flag on unknown data; keep the no-reading assertion below.
      ...(![quota.five_hour, quota.seven_day, quota.primary_used, quota.secondary_used]
        .some((value) => typeof value === "number")
        ? { primary_used: 25, primary_window_minutes: 300 } : {}),
      ...("read_at" in quota ? { read_at: hoursAgo(2).toISOString() } : { at: Math.floor(hoursAgo(2).getTime() / 1000) }),
    })),
  }));
  await walk.open("/monitor");
  await walk.state("06-stale-overlay", {
    visible: [page.getByText("Stale", { exact: true }).first(), page.getByText("reading 2h old", { exact: true }).first(), page.locator(".meter-reserve").first()],
    hidden: [loading, page.getByText(/^No reading\./)],
  });
  await fitsInViewport(page);
  await page.unroute("**/api/monitor*");

  // One engine configured: one gauge, no empty second column.
  const [first, ...rest] = seats;
  await overlay(page, "/api/monitor", (body) => ({ ...body, seats: [first] }));
  await walk.open("/monitor");
  await walk.state("07-one-engine-overlay", {
    visible: [page.getByRole("region", { name: first!.label, exact: true })],
    hidden: rest.map((seat) => page.getByRole("region", { name: seat.label, exact: true })),
  });
  await expect(page.locator(".monitor-seat")).toHaveCount(1);
  await page.unroute("**/api/monitor*");

  // No live sessions: one muted sentence.
  await overlay(page, "/api/monitor", (body) => ({ ...body, sessions: [] }));
  await walk.open("/monitor");
  await walk.state("08-no-sessions-overlay", {
    visible: [page.getByRole("heading", { name: "Sessions (0)", exact: true }), page.getByText("No live sessions.", { exact: true })],
    hidden: [page.locator(".monitor-session")],
  });
  await page.unroute("**/api/monitor*");
});

test("Monitor retains each partial window through fresh, stale and missing readings", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const baseline = await (await request.get("/api/monitor")).json();
  const [first] = baseline.seats as Seat[];
  expect(first, "the fixture supplies a configured seat").toBeTruthy();
  let quota: Json = { known: false };
  await page.clock.install();
  await page.route("**/api/monitor*", (route) => route.fulfill({ json: {
    ...baseline, seats: [{ ...first, quota }],
  } }));
  const card = page.getByRole("region", { name: first!.label, exact: true });
  const noReading = card.getByText(/^No reading\./);
  const stale = card.getByText("Stale", { exact: true });
  await walk.open("/monitor");
  await walk.state("01-no-figures", {
    visible: [noReading], hidden: [card.locator(".monitor-age"), card.locator(".monitor-meter"), stale],
  });

  const cases = [
    { state: "seven-day-only", reading: { five_hour: null, seven_day: 12 }, name: "7-day", percent: 12, absent: "5-hour" },
    { state: "five-hour-only-zero", reading: { five_hour: 0, seven_day: null }, name: "5-hour", percent: 0, absent: "7-day" },
    { state: "named-secondary-only", reading: { primary_used: null, secondary_used: 12, secondary_window_minutes: 90 }, name: "90-minute", percent: 12, absent: "first" },
    { state: "named-primary-only-zero", reading: { primary_used: 0, secondary_used: null, primary_window_minutes: 2880 }, name: "2-day", percent: 0, absent: "second" },
  ];
  for (const [i, example] of cases.entries()) {
    const reset = Math.floor(Date.now() / 1000) + 86400;
    quota = {
      ...example.reading, known: true, at: Math.floor(Date.now() / 1000),
      five_hour_resets: reset, seven_day_resets: reset,
      primary_resets: new Date(reset * 1000).toISOString(), secondary_resets: new Date(reset * 1000).toISOString(),
    };
    const visible = [card.getByText(example.name, { exact: true }), card.getByText(`${example.percent}%`, { exact: true }),
      card.getByText(`No ${example.absent} window reported.`, { exact: true }), card.getByText(/^resets in/), card.locator(".monitor-age")];
    await walk.state(`${i + 2}a-${example.state}`, {
      action: () => page.clock.fastForward(20_001), visible, hidden: [noReading, stale],
    });
    await expect(card.locator(".monitor-meter")).toHaveCount(1);
    await expect(card.locator(".meter-reserve")).toHaveCount(1);
    await expect(card.locator(".meter-fill")).toHaveAttribute("style", `width: ${example.percent}%;`);
    await fitsInViewport(page);

    quota = { ...quota, known: false, stale: true, at: Math.floor(hoursAgo(2).getTime() / 1000) };
    await walk.state(`${i + 2}b-${example.state}-stale`, {
      action: () => page.clock.fastForward(20_001),
      visible: [...visible, stale, card.getByText("reading 2h old", { exact: true })], hidden: [noReading],
    });
    await expect(card.locator(".monitor-meter[data-stale]")).toHaveCount(1);
    await fitsInViewport(page);
  }

  // Reset metadata and a stale flag alone do not create a reading or an absent-window row.
  quota = { ...quota, primary_used: null, secondary_used: null };
  await walk.state("06-no-figures-removes-stale-reading", {
    action: () => page.clock.fastForward(20_001), visible: [noReading],
    hidden: [card.locator(".monitor-age"), card.locator(".monitor-meter"), card.getByText(/window reported/), card.getByText(/^resets in/), stale],
  });
});

test("Monitor separates each model's own allowance from the shared windows", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const baseline = await (await request.get("/api/monitor")).json();
  const [first, ...rest] = baseline.seats as (Seat & { models?: Json[] })[];
  expect(first, "the fixture supplies a configured seat").toBeTruthy();
  const now = Math.floor(Date.now() / 1000);
  const shared = { known: true, at: now - 180, five_hour: 55, seven_day: 55, five_hour_resets: now + 7200, seven_day_resets: now + 86400 * 2 };
  const exhausted = { model: "alpha", label: "Alpha", seven_day: 100, seven_day_resets: now + 86400 * 3, rejected: null };
  const unread = { model: "beta", label: "Beta", seven_day: null, seven_day_resets: null, rejected: null };
  let seat: Json = { ...first, quota: shared, models: [exhausted, unread] };
  await page.route("**/api/monitor*", (route) => route.fulfill({ json: { ...baseline, seats: [seat, ...rest] } }));
  await page.clock.install();
  const card = page.getByRole("region", { name: first!.label, exact: true });
  const models = card.getByRole("list", { name: `${first!.label} models`, exact: true });
  const noModelReading = models.getByText(/^No reading for this model\./);
  const unavailable = models.getByText(/^Unavailable:/);
  const stale = card.getByText("Stale", { exact: true });

  await walk.open("/monitor");
  await walk.state("01-shared-headroom-model-exhausted", {
    visible: [card.getByText("55%", { exact: true }).first(), models.getByText("Alpha · 7-day", { exact: true }),
      models.getByText("100%", { exact: true }), models.getByText(/^resets in 3d/), noModelReading],
    hidden: [unavailable, stale],
  });
  await expect(models.locator(".meter-fill")).toHaveAttribute("style", "width: 100%;");
  await fitsInViewport(page);

  seat = { ...seat, models: [{ ...exhausted, seven_day_resets: null }, { ...unread, rejected: "beta allowance exhausted; reset time unknown" }] };
  await walk.state("02-unknown-reset-and-rejection", {
    action: () => page.clock.fastForward(20_001),
    visible: [models.getByText("reset time not reported", { exact: true }), models.getByText("Unavailable: beta allowance exhausted; reset time unknown", { exact: true }), noModelReading],
    hidden: [models.getByText(/^resets in/)],
  });
  await fitsInViewport(page);

  seat = { ...seat, models: [{ ...exhausted, seven_day: 0 }, unread] };
  await walk.state("03-zero-is-a-reading", {
    action: () => page.clock.fastForward(20_001),
    visible: [models.getByText("0%", { exact: true }), noModelReading], hidden: [unavailable, models.getByText("100%", { exact: true })],
  });

  seat = { ...seat, quota: { ...shared, known: false, stale: true, at: Math.floor(hoursAgo(2).getTime() / 1000) }, models: [exhausted, unread] };
  await walk.state("04-stale-model-reading", {
    action: () => page.clock.fastForward(20_001),
    visible: [stale, card.getByText("reading 2h old", { exact: true }), models.getByText("100%", { exact: true })], hidden: [unavailable],
  });
  await expect(models.locator(".monitor-meter[data-stale]")).toHaveCount(1);

  seat = { ...seat, quota: { known: true, at: now - 180 }, models: [{ ...exhausted, seven_day: 40 }, unread] };
  await walk.state("05-model-only-reading", {
    action: () => page.clock.fastForward(20_001),
    visible: [card.getByText("No account windows reported.", { exact: true }), card.getByText(/^reading \d+m old$/),
      models.getByText("40%", { exact: true })],
    hidden: [card.getByText(/^No reading\. /), stale],
  });
  await fitsInViewport(page);

  seat = { ...seat, quota: { known: false, why: "the seat has not been read yet" }, models: [unread] };
  await walk.state("06-no-reading-anywhere", {
    action: () => page.clock.fastForward(20_001),
    visible: [card.getByText(/^No reading\. /), noModelReading], hidden: [stale, models.locator(".monitor-meter")],
  });
  await fitsInViewport(page);
});
