import { expect, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// Real task storage, questions, dependencies and faults; the usage readings are the collector's records,
// written by the fixture with fresh times. Nothing here launches a provider or worker.
test.use({ serviceScript: "agent-graph-service.py" });

const path = "/projects/harbor";
// A reading is live for 30 seconds, so each live check writes a fresh one first.
const activity = async (request: APIRequestContext, age = 0) => {
  expect((await request.post("/fixture/activity", { data: { age } })).ok()).toBe(true);
};

test("Work's graph: the switch, every node's state and link, live helpers, aging, saved, keyboard and reduced motion @desktop-only", async ({ page, request }, info) => {
  // The saved state waits out the project read's retries.
  test.setTimeout(60_000);
  const walk = walkthrough(page, info);
  const work = page.getByRole("region", { name: "Work", exact: true });
  const views = work.getByRole("group", { name: "Show work as", exact: true });
  const list = views.getByRole("button", { name: "List", exact: true });
  const graphButton = views.getByRole("button", { name: "Graph", exact: true });
  const graph = work.getByRole("group", { name: "Current work as a graph", exact: true });
  const current = work.getByRole("region", { name: "Current", exact: true });
  const node = (name: RegExp) => graph.getByRole("link", { name });
  // The graph fits its panel: no sideways scroll beside a scrollbar.
  const fits = async (panel: typeof work) => expect(await panel.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  const sync = node(/^Add offline sync for the field app · L2 working · .* · 2 helpers active$/);
  const login = node(/^Fix the login redirect loop · Your turn · 1 question · /);
  const exporting = node(/^Rework the export pipeline · L2 working · .* · 1 helper active$/);
  const planned = node(/^Ship the export dashboard · Planned · Waiting for “Rework the export pipeline” to finish\.$/);
  const fault = node(/^Upgrade the payment SDK · /);
  const queued = node(/^Update the onboarding copy · Queued · /);

  await activity(request);
  await walk.open(path);
  await walk.state("graph-01-list-is-the-default", { visible: [list, graphButton, current], hidden: [graph] });
  await expect(list).toHaveAttribute("aria-pressed", "true");
  expect((await work.boundingBox())!.width).toBe(340);
  const titles = async (links: ReturnType<typeof current.getByRole>) =>
    (await links.evaluateAll((all) => all.map((a) => a.getAttribute("aria-label") ?? ""))).map((name) => name.split(" · ")[0]);
  const listed = await titles(current.getByRole("link"));
  expect(listed).toHaveLength(6);

  await walk.state("graph-02-graph", {
    action: () => graphButton.click(),
    visible: [graph, sync, login, exporting, planned, fault, queued, graph.getByText("L3", { exact: true })],
    hidden: [current],
  });
  await expect(graphButton).toHaveAttribute("aria-pressed", "true");
  expect((await work.boundingBox())!.width).toBe(480);
  await fits(work);
  // The operator's turn leads the ring; the rest keep the list's order.
  expect(await titles(graph.getByRole("link"))).toEqual(["Fix the login redirect loop", ...listed.filter((t) => t !== "Fix the login redirect loop")]);
  await expect(graph.locator(".ag-hub")).toHaveText("L3Ready");
  await expect(login).toHaveAttribute("data-state", "waiting");
  await expect(login.locator(".ag-count")).toHaveText("1");
  await expect(login.getByText("Your turn · 1 question", { exact: true })).toBeVisible();
  await expect(fault).toHaveAttribute("data-state", "danger");
  await expect(planned).toHaveAttribute("data-state", "idle");
  await expect(queued).toHaveAttribute("data-state", "idle");
  await expect(sync).toHaveAttribute("data-state", "running");
  await expect(sync).toHaveAttribute("data-live", "true");
  await expect(sync.locator(".ag-pip")).toHaveCount(2);
  await expect(sync.getByText("L2 working · 2 helpers active", { exact: true })).toBeVisible();
  await expect(exporting.locator(".ag-pip")).toHaveCount(1);
  await expect(planned.getByText("Planned", { exact: true })).toBeVisible();
  await expect(graph.locator(".ag-after")).toHaveCount(1);
  for (const link of [sync, login, exporting, planned, fault, queued]) {
    await expect(link).toHaveAttribute("title", (await link.getAttribute("aria-label"))!);
  }
  await expect(sync).toHaveAttribute("href", /\/projects\/harbor\/tasks\/add-offline-sync-for-the-field-app$/);
  await expect(login).toHaveAttribute("href", /\/projects\/harbor\/(tasks\/fix-the-login-redirect-loop|needs)/);

  await activity(request);
  await walk.state("graph-03-remembered-after-reload", { action: () => page.reload(), visible: [graph, sync], hidden: [current] });
  expect(await page.evaluate(() => localStorage.getItem("altitude.workView"))).toBe("graph");

  // Keyboard: the switch, then each node in ring order; the coordinator is not a stop. Enter opens.
  await graphButton.focus();
  await page.keyboard.press("Tab");
  await expect(login).toBeFocused();
  await page.keyboard.press("Tab");
  const second = graph.getByRole("link").nth(1);
  await expect(second).toBeFocused();
  await walk.state("graph-04-keyboard-focus", { visible: [second], hidden: [] });
  const href = (await second.getAttribute("href"))!;
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`${href}$`));
  await page.goBack();
  await expect(graph).toBeVisible();

  await activity(request);
  await expect(sync).toHaveAttribute("data-live", "true");
  await page.emulateMedia({ reducedMotion: "reduce" });
  const disc = sync.locator(".ag-disc");
  await walk.state("graph-05-reduced-motion", { visible: [sync], hidden: [] });
  expect(await disc.evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
  expect(await disc.evaluate((el) => getComputedStyle(el).boxShadow)).toMatch(/0px 0px 0px 4px$/);
  expect(await graph.locator(".ag-node").first().evaluate((el) => getComputedStyle(el).transitionDuration)).toBe("0s");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  expect(await disc.evaluate((el) => getComputedStyle(el).animationName)).toBe("ag-breathe");

  // A reading older than half a minute claims nothing live, and says helper activity is unknown.
  await activity(request, 45);
  const aged = node(/^Add offline sync for the field app · L2 working · [^·]+ · started [^·]+ · helper activity unknown$/);
  await walk.state("graph-06-aged-reading", { visible: [aged], hidden: [sync.locator(".ag-pip"), exporting] });
  await expect(aged).not.toHaveAttribute("data-live");

  // Saved after a failed refresh: nodes stay and open; no halo, pip or coordinator word.
  await activity(request);
  await expect(sync).toHaveAttribute("data-live", "true");
  const projectRead = (url: URL) => url.pathname === "/api/project/harbor";
  await page.route(projectRead, (route) => route.fulfill({ status: 503, json: { error: "Project read unavailable." } }));
  await activity(request);
  // The failed refresh is retried before the panel shows the saved notice.
  await expect(work.getByText("Showing saved work.", { exact: false })).toBeVisible({ timeout: 20_000 });
  await walk.state("graph-07-saved", {
    visible: [work.getByText("Showing saved work.", { exact: false }), aged, login],
    hidden: [graph.locator(".ag-pip"), graph.locator(".ag-hub-word"), graph.locator("[data-live]")],
  });
  await page.unroute(projectRead);
  await work.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(work.getByText("Showing saved work.", { exact: false })).toBeHidden();

  await walk.state("graph-08-back-to-list", {
    action: () => list.click(), visible: [current, current.getByRole("link", { name: /^Fix the login redirect loop/ })], hidden: [graph],
  });
  expect(await page.evaluate(() => localStorage.getItem("altitude.workView"))).toBe("list");
  expect((await work.boundingBox())!.width).toBe(340);

  // Below the inline width the Work overlay takes the graph's width too.
  await graphButton.click();
  await page.setViewportSize({ width: 1100, height: 900 });
  await page.getByRole("button", { name: "Work panel", exact: true }).click();
  const overlay = page.locator(".overlay-panel");
  await walk.state("graph-09-overlay", { visible: [overlay.getByRole("group", { name: "Current work as a graph", exact: true })], hidden: [] });
  expect((await overlay.boundingBox())!.width).toBe(480);
  await fits(overlay);
  await fits(overlay.locator(".work-panel"));
});

test("the phone's Work shows the list without a switch, whatever the browser chose @phone-only", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  await page.addInitScript(() => localStorage.setItem("altitude.workView", "graph"));
  const work = page.getByRole("region", { name: "Work", exact: true });
  await walk.open(`${path}?tab=work`);
  await walk.state("graph-phone-list", {
    visible: [work.getByRole("region", { name: "Current", exact: true }).getByRole("link", { name: /^Add offline sync for the field app/ })],
    hidden: [work.getByRole("group", { name: "Show work as" }), work.getByRole("group", { name: "Current work as a graph" })],
  });
});
