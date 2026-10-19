import { expect, type Route } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Repairs in Settings → About (SPEC.md §3.15, docs/OPERATIONS.md#repairs), walked at both widths: the Repair row on a
 * computer without repairs, loading, a failed load and its Retry, a running repair with recent repairs and All repairs,
 * the full list and its empty state, and each outcome's page. The service is real; the repairs are fictional fixtures,
 * since a repair runs only from the operator's own terminal.
 */

const id = (digit: string) => digit.repeat(32);
const RUNNING = { id: id("a"), title: "The page does not load on my phone", route: "terminal", engine: "claude", started: "2026-10-19T09:10:00Z", ended: null, outcome: null };
const ROWS = [
  RUNNING,
  { id: id("b"), title: "Failed update to 0.9.0", route: "terminal", engine: "claude", started: "2026-10-18T08:00:00Z", ended: "2026-10-18T08:20:00Z", outcome: "repaired" },
  { id: id("c"), title: "Tasks stopped starting after I moved the projects folder somewhere else entirely", route: "terminal", engine: "claude", started: "2026-10-17T08:00:00Z", ended: "2026-10-17T08:30:00Z", outcome: "changed_by_hand" },
  { id: id("d"), title: "Repair", route: "terminal", engine: "claude", started: "2026-10-16T08:00:00Z", ended: "2026-10-16T08:05:00Z", outcome: "no_report" },
];
const DETAIL: Record<string, object> = {
  [id("a")]: { ...RUNNING, problem: RUNNING.title, update: null, report: null, check: null, brief: "# Repair brief\n\nFind out what is wrong and fix it." },
  [id("b")]: { ...ROWS[1], problem: null, update: { version: "v0.9.0", error: "The download failed its certificate check." },
    report: { wrong: "The update's download was refused by an expired certificate.", changed: "Renewed the certificate and ran alt update again.", remains: "Nothing." },
    check: { at: "2026-10-18T08:21:00Z", version: "v0.9.0", running: "v0.9.0", matches: true, failing: [] }, brief: "# Repair brief" },
  [id("c")]: { ...ROWS[2], problem: ROWS[2].title, update: null,
    report: { wrong: "The service still pointed at the old projects folder.", changed: "Set the new folder and restarted the service.", remains: "Nothing known.", try: "Start a task in atlas." },
    check: { at: "2026-10-17T08:31:00Z", version: "v0.9.0", running: "v0.9.0", matches: false, failing: [] }, brief: "# Repair brief" },
  [id("d")]: { ...ROWS[3], problem: null, update: null, report: null,
    check: { at: "2026-10-16T08:06:00Z", version: "v0.9.0", running: "v0.9.0", matches: true, failing: [{ name: "github cli" }] }, brief: "# Repair brief" },
};

test("repairs in Settings → About: start, loading, failure, recent repairs, every repair and each outcome's page", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const about = page.getByRole("region", { name: "About" });
  // Settings scrolls inside its own pane, so each overview capture brings About into view.
  const show = () => about.scrollIntoViewIfNeeded();
  const start = page.getByText(/Something not working\? Run this in a terminal on this computer/);
  const command = about.getByText("alt repair", { exact: true });
  const runningMark = about.getByText("Running in a terminal");
  const loading = page.getByText("Loading repairs…");
  const failed = page.getByRole("alert").filter({ hasText: "Could not load repairs." });
  const allRepairs = page.getByRole("link", { name: /All repairs/ });
  const row = (title: string | RegExp) => page.getByRole("link", { name: title });

  await walk.open("/settings");
  await walk.state("01-no-repairs", { action: show, visible: [start, command], hidden: [runningMark, loading, failed, allRepairs] });

  // One fixture answers the listing: held while loading, failing (every retry too) until Retry, then the repairs.
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  let listing: { running: object | null; repairs: object[] } | null = null;
  await page.route("**/api/repairs", async (route) => {
    if (listing) return route.fulfill({ json: listing });
    await held;
    return route.fulfill({ status: 500, json: { error: "fixture" } });
  });
  await page.route(/\/api\/repairs\/[^/]+$/, (route: Route) => {
    const found = DETAIL[route.request().url().split("/").pop()!];
    return found ? route.fulfill({ json: found }) : route.fulfill({ status: 404, json: { error: "That repair is not on this computer." } });
  });
  await page.reload();
  await walk.state("02-loading", { action: show, visible: [start, loading], hidden: [failed] });
  release();
  await expect(failed).toBeVisible({ timeout: 20_000 });
  await walk.state("03-load-failed", { action: show, visible: [start, failed, failed.getByRole("button", { name: "Retry" })], hidden: [loading] });

  listing = { running: { id: RUNNING.id, route: "terminal" }, repairs: ROWS };
  await failed.getByRole("button", { name: "Retry" }).click();
  const running = row(/The page does not load on my phone/);
  const repaired = row(/Failed update to 0\.9\.0/);
  const byHand = row(/Tasks stopped starting/);
  await walk.state("04-running-and-recent", {
    action: show,
    visible: [start, runningMark, running, running.getByText("Running"), repaired, repaired.getByText("✓ Repaired"),
      byHand, byHand.getByText("Confirm yourself"), byHand.getByText("From a terminal"), allRepairs, allRepairs.getByText("4 repairs")],
    hidden: [command, failed, row(/Oct 16 · Repair/)],
  });
  const title = byHand.locator(".repair-row-title");
  expect(await title.evaluate((node) => node.scrollWidth > node.clientWidth), "a long title stays on one line").toBe(true);

  await allRepairs.click();
  await expect(page).toHaveURL(/\/settings\/repairs$/);
  const noReport = row(/Oct 16 · Repair/);
  await walk.state("05-all-repairs", { visible: [page.getByRole("heading", { name: "Repairs" }), running, repaired, byHand, noReport, noReport.getByText("✕ Not repaired")], hidden: [start] });

  await repaired.click();
  await expect(page).toHaveURL(new RegExp(`/settings/repairs/${id("b")}$`));
  const brief = page.getByText("Brief", { exact: true });
  await walk.state("06-repaired", {
    visible: [page.getByRole("heading", { name: "Failed update to 0.9.0" }), page.getByRole("link", { name: "‹ Repairs" }), page.getByText("✓ Repaired"),
      page.getByText(/^From a terminal · started .* · ended /), page.getByText("The update's download was refused by an expired certificate."),
      page.getByText("Renewed the certificate and ran alt update again."), page.getByText("Altitude checked: version 0.9.0 · matches its release · all checks pass"),
      page.getByText("Its steps are in that terminal’s history."), brief],
    hidden: [page.getByText(/^Try:/), page.getByText("The repair ended without a report."), page.getByText(/still runs|is not installed/), page.getByText("# Repair brief")],
  });
  await brief.click();
  await expect(page.getByText("# Repair brief")).toBeVisible();

  await page.getByRole("link", { name: "‹ Repairs" }).click();
  await byHand.click();
  await walk.state("07-changed-by-hand", {
    visible: [page.getByText("Confirm yourself · the installation was changed by hand."), page.getByText("Try:"), page.getByText("Start a task in atlas."),
      page.getByText("The installed files differ from the release: the installation was changed by hand.")],
    hidden: [page.getByText(/matches its release/), page.getByText(/^Failing:/)],
  });

  await page.getByRole("link", { name: "‹ Repairs" }).click();
  await noReport.click();
  await walk.state("08-no-report", {
    visible: [page.getByText("✕ Not repaired"), page.getByText("The repair ended without a report."), page.getByText("Failing: github cli")],
    hidden: [page.getByRole("heading", { name: "What was wrong" }), page.getByText(/all checks pass/)],
  });

  await page.getByRole("link", { name: "‹ Repairs" }).click();
  await running.click();
  await walk.state("09-running", {
    visible: [page.getByRole("heading", { name: "The page does not load on my phone" }), page.getByText("Running"), page.getByText("Altitude checks the result once the repair ends.")],
    hidden: [page.getByText("The repair ended without a report."), page.getByText(/ · ended /)],
  });

  // The repair has ended and Altitude's check has not run yet.
  const ended = { ...RUNNING, ended: "2026-10-19T09:40:00Z" };
  listing = { running: null, repairs: [ended, ...ROWS.slice(1)] };
  DETAIL[RUNNING.id] = { ...DETAIL[RUNNING.id], ended: ended.ended };
  await page.reload();
  await walk.state("10-checking", {
    visible: [page.getByText("Checking", { exact: true }), page.getByText("The repair ended without a report."), page.getByText("Altitude is checking the result…"), page.getByText(/ · ended /)],
    hidden: [page.getByText("Running", { exact: true }), page.getByText("Altitude checks the result once the repair ends.")],
  });

  listing = { running: null, repairs: [] };
  await page.goto("/settings/repairs");
  await walk.state("11-no-repairs-page", { visible: [page.getByText("No repairs yet. Run alt repair in a terminal on this computer to start one.")], hidden: [running] });
  await page.goto(`/settings/repairs/${id("e")}`);
  await walk.state("12-missing-repair", { visible: [page.getByText("That repair is not on this computer.")], hidden: [page.getByRole("alert"), page.getByText("Brief", { exact: true })] });
});
