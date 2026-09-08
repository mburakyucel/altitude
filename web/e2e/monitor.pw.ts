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
  await page.route(`**${path}*`, async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: patch((await response.json()) as Json) });
  });
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
      ...(typeof quota.primary_used !== "number" &&
          !(typeof quota.five_hour === "number" && typeof quota.seven_day === "number")
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
