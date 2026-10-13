import { expect, type Page, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "document-update-service.py" });
test.beforeAll(async ({ browser }, info) => {
  await info.attach("document-browser.json", { contentType: "application/json",
    body: JSON.stringify({ browser: info.project.use.browserName, version: browser.version() }) });
});
type Entry = "shown" | "file" | "system";
const entries: Entry[] = ["shown", "file", "system"];
const draft = (page: Page, entry: Entry) => page.getByRole("textbox", {
  name: entry === "shown" ? "Message the L2" : "Message L3 about atlas", exact: true,
});

async function prepare(page: Page, request: APIRequestContext, entry: Entry, interceptFile = true) {
  const { slug, path, source } = await (await request.put("/fixture/documents")).json();
  let release = () => {};
  if (entry === "file" && interceptFile) {
    const gate = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/api/files/atlas?*", async route => { await gate; await route.continue(); });
  }
  const route = entry === "file" ? `/projects/atlas/file?${new URLSearchParams({ path })}`
    : entry === "shown" ? `/projects/atlas/tasks/${slug}` : "/projects/atlas";
  expect((await page.goto(route))?.ok()).toBe(true);
  if (entry !== "file") await draft(page, entry).fill("Keep this unsent draft.");
  else if (interceptFile) await expect(page.getByText("Loading file…", { exact: true })).toBeVisible();
  return {
    source: source as string,
    async show() {
      if (entry === "file") release();
      else if (entry === "shown") await page.getByRole("link", { name: "Open PLAN.md", exact: true }).click();
      else await page.getByRole("button", { name: "Show", exact: true }).click();
    },
    async close() {
      if (entry === "shown") await page.getByRole("region", { name: "PLAN.md", exact: true }).getByRole("button", { name: "Close", exact: true }).click();
      else if (entry === "system") await page.getByRole("button", { name: "Hide", exact: true }).click();
      if (entry !== "file") await expect(draft(page, entry)).toHaveValue("Keep this unsent draft.");
    },
  };
}

for (const entry of entries) {
  for (const asset of ["js", "css", "both"] as const) {
    test(`document survives a built update: ${entry} ${asset}`, async ({ page, request }, info) => {
      const view = await prepare(page, request, entry);
      const walk = walkthrough(page, info);
      const chunks: string[] = [];
      page.on("response", response => {
        if (/\/assets\/MarkdownDocument-/.test(response.url())) chunks.push(`${response.status()} ${new URL(response.url()).pathname}`);
      });
      let reloads = 0;
      page.on("load", () => { reloads += 1; });
      const update = await request.put("/fixture/update", { data: { asset } });
      expect(update.ok()).toBe(true);
      const { removed } = await update.json() as { removed: string[] };
      const missing = removed.filter(name => asset === "css" ? name.endsWith(".css")
        : name.startsWith("MarkdownDocument-") && name.endsWith(".js"));
      expect(missing.length).toBeGreaterThan(0);
      for (const name of missing) expect((await request.get(`/assets/${name}`)).status()).toBe(404);
      await view.show();
      const notice = page.getByText("Formatting unavailable", { exact: true });
      const reload = page.getByRole("button", { name: "Reload page", exact: true });
      const router = page.getByText("Unexpected Application Error!", { exact: true });
      if (asset === "css") {
        await walk.state(`${entry}-removed-css-still-formatted`, {
          visible: [page.getByRole("heading", { name: "Migration plan", exact: true })], hidden: [notice, reload, router],
        });
        expect(chunks.some(chunk => chunk.startsWith("200 "))).toBe(true);
      } else {
        await walk.state(`${entry}-${asset}-readable-source`, {
          visible: [notice, reload, page.locator(".document-source")], hidden: [router],
        });
        expect(await page.locator(".document-source").textContent()).toBe(view.source);
        await expect(page.locator(".document-source a, .document-source img, .document-source button")).toHaveCount(0);
        const warning = page.getByText("Reloading clears text you have typed but not sent.", { exact: true });
        if (entry === "file") await expect(warning).toBeHidden();
        else await expect(warning).toBeVisible();
        expect(chunks.some(chunk => chunk.startsWith("404 "))).toBe(true);
        if (entry === "shown") {
          const image = page.getByRole("button", { name: "Open image diagram.png", exact: true });
          await image.click();
          await expect(page.getByRole("dialog").getByRole("img")).toBeVisible();
          await page.getByRole("button", { name: "Close image", exact: true }).click();
          await expect(notice).toBeVisible();
        }
      }
      expect(reloads, "Nothing reloads automatically").toBe(0);
      await expect(page.locator('link[rel="stylesheet"][href*="MarkdownDocument-"]')).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      if (asset !== "css") {
        if (entry === "shown") {
          await view.close();
          await page.getByRole("link", { name: "Open SECOND.md", exact: true }).click();
          await expect(page.locator(".document-source")).toContainText("# Second plan");
          await page.getByRole("region", { name: "SECOND.md", exact: true }).getByRole("button", { name: "Close", exact: true }).click();
          await expect(draft(page, entry)).toHaveValue("Keep this unsent draft.");
          await view.show();
        } else if (entry === "file") {
          await page.getByRole("button", { name: "Raw", exact: true }).click();
          await expect(notice).toBeHidden();
          await expect(page.locator(".task-file-raw")).toHaveText(view.source);
          await page.getByRole("button", { name: "Raw", exact: true }).click();
        } else {
          await view.close();
          await view.show();
        }
        await reload.click();
        if (entry === "system") await page.getByRole("button", { name: "Show", exact: true }).click();
        await walk.state(`${entry}-${asset}-explicit-reload-formatted`, {
          visible: [page.getByRole("heading", { name: "Migration plan", exact: true })], hidden: [notice, router],
        });
        if (entry === "shown") {
          await page.getByRole("region", { name: "PLAN.md", exact: true }).getByRole("button", { name: "Close", exact: true }).click();
          await expect(draft(page, entry)).toHaveValue("");
        }
      } else await view.close();
    });
  }

  test(`connection drops during formatting load with the browser cache enabled: ${entry}`, async ({ browser, altitude, request }, info) => {
    // No interception: exercise WebKit's real cache and refetch of the same script after a failed load.
    const context = await browser.newContext({ ...info.project.use, baseURL: altitude.url });
    await context.addCookies([{ name: "altitude_device", value: altitude.device, url: altitude.url }]);
    const page = await context.newPage();
    try {
      const chunks: string[] = [];
      page.on("request", sent => {
        if (/\/assets\/MarkdownDocument-.*\.js$/.test(sent.url())) chunks.push(new URL(sent.url()).pathname);
      });
      // The standalone file starts its read on entry; dropping code first avoids interception to hold it.
      expect((await request.put("/fixture/unreachable")).ok()).toBe(true);
      const view = await prepare(page, request, entry, false);
      await view.show();
      await walkthrough(page, info).state(`${entry}-connection-failure-readable`, {
        visible: [page.locator(".document-source"), page.getByRole("button", { name: "Reload page", exact: true })],
        hidden: [page.getByText("Unexpected Application Error!", { exact: true })],
      });
      expect(await page.locator(".document-source").textContent()).toBe(view.source);
      await expect(page.locator('link[rel="modulepreload"]')).toHaveCount(0);
      expect(chunks).toHaveLength(1);
      expect((await request.put("/fixture/back")).ok()).toBe(true);
      await page.getByRole("button", { name: "Reload page", exact: true }).click();
      if (entry === "system") await page.getByRole("button", { name: "Show", exact: true }).click();
      await expect(page.getByRole("heading", { name: "Migration plan", exact: true })).toBeVisible();
      await expect(page.getByText("Formatting unavailable", { exact: true })).toBeHidden();
      expect(chunks).toEqual([chunks[0], chunks[0]]);
    } finally { await context.close(); }
  });

  test(`a renderer runtime error is not a load failure: ${entry}`, async ({ page, request }) => {
    const view = await prepare(page, request, entry);
    expect((await request.put("/fixture/renderer-error")).ok()).toBe(true);
    await view.show();
    await expect(page.getByText("Unexpected Application Error!", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Fictional renderer failure", exact: true })).toBeVisible();
    await expect(page.getByText("Formatting unavailable", { exact: true })).toBeHidden();
  });
}

for (const entry of ["file", "shown"] as const) {
  test(`failed content transport retains API Retry: ${entry}`, async ({ page, request }, info) => {
    // A transport overlay affects only the first content read; Retry still uses the real API/storage.
    const content = entry === "file" ? "**/api/files/atlas?*" : "**/api/shown/atlas/**";
    await page.route(content, route => route.abort("connectionfailed"), { times: 1 });
    const view = await prepare(page, request, entry);
    await view.show();
    const title = entry === "file" ? "File unavailable" : "Preview unavailable";
    await walkthrough(page, info).state(`${entry}-failed-content`, {
      visible: [page.getByRole("heading", { name: title, exact: true }), page.getByRole("button", { name: "Retry", exact: true })],
      hidden: [page.getByText("Formatting unavailable", { exact: true })],
    });
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Migration plan", exact: true })).toBeVisible();
    await view.close();
  });
  test(`offline content read resumes on reconnect: ${entry}`, async ({ page, request, context }, info) => {
    let view: Awaited<ReturnType<typeof prepare>> | undefined;
    if (entry === "file") {
      const { path } = await (await request.put("/fixture/documents")).json();
      expect((await page.goto("/projects/atlas"))?.ok()).toBe(true);
      await expect(draft(page, "system")).toBeVisible();
      await context.setOffline(true);
      // Start the read after going offline, using the real router's history event. Holding an already
      // in-flight request in interception lets WebKit complete it during the offline transition.
      await page.evaluate(route => {
        window.history.pushState(null, "", route);
        window.dispatchEvent(new PopStateEvent("popstate"));
      }, `/projects/atlas/file?${new URLSearchParams({ path })}`);
    } else {
      view = await prepare(page, request, entry);
      await context.setOffline(true);
      await view.show();
    }
    await walkthrough(page, info).state(`${entry}-offline-content`, {
      visible: [entry === "file" ? page.getByText("Loading file…", { exact: true })
        : page.getByRole("status", { name: "Loading PLAN.md", exact: true })],
      hidden: [page.getByText("Formatting unavailable", { exact: true })],
    });
    await context.setOffline(false);
    await expect(page.getByRole("heading", { name: "Migration plan", exact: true })).toBeVisible();
    await view?.close();
  });
}

test("offline formatting retains source until explicit reload", async ({ page, request, context }, info) => {
  const view = await prepare(page, request, "system");
  await context.setOffline(true);
  await view.show();
  await walkthrough(page, info).state("system-offline-formatting", {
    visible: [page.locator(".document-source"), page.getByText("Formatting unavailable", { exact: true })],
    hidden: [page.getByText("Unexpected Application Error!", { exact: true })],
  });
  await context.setOffline(false);
  await view.close();
  await view.show();
  await expect(page.locator(".document-source")).toHaveText(view.source);
  await page.getByRole("button", { name: "Reload page", exact: true }).click();
  await page.getByRole("button", { name: "Show", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Migration plan", exact: true })).toBeVisible();
});
