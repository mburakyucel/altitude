import { expect, test, type Page } from "@playwright/test";
import { walkthrough } from "./walkthrough";

/**
 * Monitor states (SPEC.md §3.14, slice 5). Ready, loading and error come from the running service;
 * a state it cannot produce on demand (no reading, a stale reading, one engine, no sessions) is the
 * real response with the one field changed, and its name says so with "-overlay". Nothing is written.
 */
type Json = Record<string, unknown>;

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
  const overview = (await (await request.get("/api/overview")).json()) as { engines: Array<{ engine: string; label: string }> };
  expect(overview.engines.length, "the seam reports at least one configured engine").toBeGreaterThan(0);
  const loading = page.getByLabel("Loading", { exact: true });
  const routing = page.getByRole("heading", { name: "Routing now", exact: true });
  const seats = page.getByRole("heading", { name: "Seats", exact: true });
  const sessions = page.getByRole("heading", { name: /^Sessions \(\d+\)$/ });
  // The sentence and its Retry share one paragraph: match the start, not the whole text.
  const error = page.getByText(/^Could not read the monitor\./);
  const retry = page.getByRole("button", { name: "Retry", exact: true });

  // Loading: the real read, held until the skeleton is on screen.
  let release = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/monitor*", async (route) => { await held; await route.continue(); });
  await walk.open("/monitor");
  await walk.state("01-loading", { visible: [loading, page.getByRole("heading", { name: "Monitor", exact: true })], hidden: [routing, error] });
  await walk.state("02-ready", { action: async () => { release(); }, visible: [seats, routing, sessions], hidden: [loading, error] });
  await page.unroute("**/api/monitor*");
  for (const row of overview.engines) await expect(page.getByRole("region", { name: row.label, exact: true })).toBeVisible();
  await fitsInViewport(page);

  // Error: every read fails until Retry is pressed; the app retries three times first.
  let failing = true;
  await page.route("**/api/monitor*", async (route) => {
    if (failing) await route.fulfill({ status: 500, json: { error: "monitor unavailable" } });
    else await route.continue();
  });
  await walk.open("/monitor");
  await error.waitFor({ timeout: 30_000 });
  await walk.state("03-error", { visible: [error, retry], hidden: [loading, routing, seats] });
  await walk.state("04-retry", {
    action: async () => { failing = false; await retry.click(); },
    visible: [seats, routing, sessions], hidden: [error, retry, loading],
  });
  await page.unroute("**/api/monitor*");

  // No reading: neither seat has ever been read.
  await overlay(page, "/api/monitor", (body) => ({
    ...body,
    quota: { known: false },
    quota_codex: { known: false, why: "the seat has not been read yet" },
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
    quota: { ...(body.quota as Json), known: false, stale: true, at: Math.floor(hoursAgo(2).getTime() / 1000) },
    quota_codex: { ...(body.quota_codex as Json), known: false, stale: true, read_at: hoursAgo(2).toISOString() },
  }));
  await walk.open("/monitor");
  await walk.state("06-stale-overlay", {
    visible: [page.getByText("Stale", { exact: true }).first(), page.getByText("reading 2h old", { exact: true }).first(), page.locator(".meter-reserve").first()],
    hidden: [loading, page.getByText(/^No reading\./)],
  });
  await fitsInViewport(page);
  await page.unroute("**/api/monitor*");

  // One engine configured: one gauge, no empty second column.
  const [first, ...rest] = overview.engines;
  await overlay(page, "/api/overview", (body) => ({ ...body, engines: [first] }));
  await walk.open("/monitor");
  await walk.state("07-one-engine-overlay", {
    visible: [page.getByRole("region", { name: first!.label, exact: true })],
    hidden: rest.map((row) => page.getByRole("region", { name: row.label, exact: true })),
  });
  await expect(page.locator(".monitor-seat")).toHaveCount(1);
  await page.unroute("**/api/overview*");

  // No live sessions: one muted sentence.
  await overlay(page, "/api/monitor", (body) => ({ ...body, sessions: [] }));
  await walk.open("/monitor");
  await walk.state("08-no-sessions-overlay", {
    visible: [page.getByRole("heading", { name: "Sessions (0)", exact: true }), page.getByText("No live sessions.", { exact: true })],
    hidden: [page.locator(".monitor-session")],
  });
  await page.unroute("**/api/monitor*");
});
