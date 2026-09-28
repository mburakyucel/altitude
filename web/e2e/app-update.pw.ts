import { expect, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/*
 * A page opened before an update keeps running the earlier build (docs/ARCHITECTURE.md#web-delivery). The
 * terminal's code loads only when a terminal is first shown, so an update in between replaces the file that
 * page asks for. The fixture's update is a real swap of the served build: new file names, the old files
 * gone, answered by altd's own 404. Walked at both widths; nothing reloads until the operator chooses Reload.
 */
test.use({ serviceScript: "terminal-service.py" });

const TASK = "/projects/atlas/tasks/prepare-index-migration";

async function fixture(request: APIRequestContext, path: string) {
  expect((await request.post(`/fixture/${path}`, { data: {} })).ok()).toBe(true);
}

test("an update between opening the page and the first terminal: the view says so and Reload opens it on the new build", async ({ page, request }, info) => {
  test.setTimeout(90_000);
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const panel = page.getByRole("region", { name: "Terminal" });
  const views = phone ? page.getByRole("navigation", { name: "Task views" }) : page.getByRole("navigation", { name: "Panel view" });
  const routerError = page.getByText("Unexpected Application Error!");
  expect((await request.post("/api/terminal-access", { data: { enabled: true } })).ok()).toBe(true);

  await walk.open(TASK);
  await fixture(request, "update");
  const chunks: string[] = [];
  page.on("response", (response) => { if (/\/assets\/TerminalScreen-/.test(response.url())) chunks.push(`${response.status()} ${new URL(response.url()).pathname}`); });
  let loads = 0;
  page.on("load", () => { loads += 1; });
  await views.getByRole("link", { name: "Terminal" }).click();
  const reload = panel.getByRole("button", { name: "Reload" });
  await walk.state("update-01-terminal-after-update", {
    visible: [panel.getByText("Altitude was updated"), panel.getByText("Reloading clears text you have typed but not sent."), reload],
    hidden: [routerError, page.locator(".terminal-screen")],
  });
  expect(chunks.some((chunk) => chunk.startsWith("404 ")), "The earlier build's terminal code is gone").toBe(true);
  expect(loads, "Nothing reloads by itself").toBe(0);

  await reload.click();
  await walk.state("update-02-reloaded-terminal", {
    visible: [page.locator(".terminal-screen .xterm-rows")],
    hidden: [routerError, panel.getByText("Altitude was updated")],
  });
  await expect(page.locator(".terminal-screen .xterm-rows")).toContainText("$");
  expect(chunks.at(-1), "The new build's terminal code").toMatch(/^200 \/assets\/TerminalScreen-updated-/);
});

test("the terminal's code cannot be fetched: the view says so without reloading, and Reload opens it once Altitude answers", async ({ browser, altitude, request }, info) => {
  test.setTimeout(90_000);
  // Altitude itself drops the connection for the terminal's code and the page's check of the current page, in
  // a context of its own: the shared context's request interception turns the browser's cache off, and the
  // browser must not keep the failure for the reload (WebKit keeps a failed script preload).
  const context = await browser.newContext({ ...info.project.use, baseURL: altitude.url });
  await context.addCookies([{ name: "altitude_device", value: altitude.device, url: altitude.url }]);
  const page = await context.newPage();
  try {
    const phone = info.project.name === "phone";
    const walk = walkthrough(page, info);
    const panel = page.getByRole("region", { name: "Terminal" });
    const views = phone ? page.getByRole("navigation", { name: "Task views" }) : page.getByRole("navigation", { name: "Panel view" });
    expect((await request.post("/api/terminal-access", { data: { enabled: true } })).ok()).toBe(true);

    await walk.open(TASK);
    let loads = 0;
    page.on("load", () => { loads += 1; });
    const chunks: string[] = [];
    page.on("request", (sent) => { if (/\/assets\/TerminalScreen-/.test(sent.url())) chunks.push(new URL(sent.url()).pathname); });
    await fixture(request, "unreachable");
    await views.getByRole("link", { name: "Terminal" }).click();
    const reload = panel.getByRole("button", { name: "Reload" });
    await walk.state("update-03-terminal-code-unreachable", {
      visible: [panel.getByText("Couldn't load the terminal"), panel.getByText(/Check the connection to Altitude, then reload\./), reload],
      hidden: [page.getByText("Unexpected Application Error!"), page.locator(".terminal-screen"), panel.getByText("Altitude was updated")],
    });
    await page.waitForTimeout(1000);
    expect(loads, "Nothing reloads by itself").toBe(0);
    expect(chunks, "The terminal's code is one script").toHaveLength(1);
    await expect(page.locator('link[rel="modulepreload"]'), "Its import alone loads it, never a script preload").toHaveCount(0);

    await fixture(request, "back");
    await reload.click();
    await walk.state("update-04-reloaded-after-outage", {
      visible: [page.locator(".terminal-screen .xterm-rows")],
      hidden: [panel.getByText("Couldn't load the terminal")],
    });
    await expect(page.locator(".terminal-screen .xterm-rows")).toContainText("$");
    expect(chunks, "The reloaded page asks Altitude for the same code again").toEqual([chunks[0], chunks[0]]);
  } finally {
    await context.close();
  }
});

test("the check of the served page stalls: the Reload card still appears", async ({ page, request }, info) => {
  test.setTimeout(60_000);
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const panel = page.getByRole("region", { name: "Terminal" });
  const views = phone ? page.getByRole("navigation", { name: "Task views" }) : page.getByRole("navigation", { name: "Panel view" });
  expect((await request.post("/api/terminal-access", { data: { enabled: true } })).ok()).toBe(true);

  await walk.open(TASK);
  await page.route((url) => url.pathname.startsWith("/assets/TerminalScreen-"), (route) => route.abort("internetdisconnected"));
  await page.route((url) => url.pathname === "/", () => {});  // never answers
  await views.getByRole("link", { name: "Terminal" }).click();
  // The check gives up after five seconds.
  await expect(panel.getByText("Couldn't load the terminal")).toBeVisible({ timeout: 15_000 });
  await walk.state("update-05-terminal-check-stalled", {
    visible: [panel.getByText("Couldn't load the terminal"), panel.getByRole("button", { name: "Reload" })],
    hidden: [page.getByText("Unexpected Application Error!")],
  });
});
