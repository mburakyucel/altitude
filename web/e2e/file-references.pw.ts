import { expect, type Locator } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "file-references-service.py" });
test.setTimeout(60_000);
const fileRoute = (path: string) => `/projects/alpha/file?${new URLSearchParams({ path })}`;

async function references(prose: Locator, paths: Record<string, string>) {
  for (const [label, path] of [[`file://${paths["commands.md"]}`, `file://${paths["commands.md"]}`], [paths["notes.txt"], paths["notes.txt"]], ["setup guide", paths["commands.md"]]]) {
    const link = prose.getByRole("link", { name: label, exact: true });
    await expect(link).toHaveAttribute("href", `/projects/alpha/file?path=${encodeURIComponent(path)}`);
    await expect(link).toHaveAttribute("title", path);
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
    await expect(link).toHaveCSS("text-decoration-line", "underline");
  }
  await expect(prose.getByRole("link", { name: "normal web links" })).toHaveAttribute("href", "https://example.test/docs");
  await expect(prose.locator("code")).toHaveText("file:///tmp/code.md");
  await expect(prose.locator("pre")).toHaveText("cat /tmp/code-block.md");
  await expect(prose.locator("code a, pre a, a a")).toHaveCount(0);
}

test("saved L2 and L3 links open rendered documents while preserving conversation and draft", async ({ page, context, request, service }, info) => {
  const { paths, source, slug } = await (await request.get(`${service}/fixture/files`)).json();
  const walk = walkthrough(page, info);
  await walk.open(`${service}/projects/alpha`);
  const l3 = page.getByRole("region", { name: "Conversation", exact: true });
  await references(l3.locator(".reply"), paths);
  await walk.state("01-saved-l3-file-links", { visible: [l3], hidden: [] });
  await walk.open(`${service}/projects/alpha/tasks/${slug}`);
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const prose = conversation.locator('.msg-row[data-role="l2"] .reply');
  await references(prose, paths);
  await page.reload();
  await references(prose, paths);
  const draft = conversation.getByRole("textbox");
  await draft.fill("Keep my unsent follow-up.");
  const link = prose.getByRole("link", { name: `file://${paths["commands.md"]}`, exact: true });
  await link.scrollIntoViewIfNeeded();
  await link.focus();
  await walk.state("02-file-link-keyboard-focus-and-draft", { visible: [link], hidden: [] });
  const scroll = await page.evaluate(() => ({ top: window.scrollY, regions: [...document.querySelectorAll(".conversation-scroll, .convo-scroll")].map((element) => element.scrollTop) }));
  const popupPromise = page.waitForEvent("popup");
  if (info.project.name === "phone") await link.tap();
  else await page.keyboard.press("Enter");
  const popup = await popupPromise;
  const reader = walkthrough(popup, info);
  await expect(popup.getByRole("heading", { name: "commands.md", exact: true })).toBeVisible();
  await expect(popup.getByLabel("Referenced path")).toHaveText(`file://${paths["commands.md"]}`);
  const contents = popup.getByRole("region", { name: "File contents" });
  await expect(contents.getByRole("heading", { name: "Setup instructions", level: 2 })).toBeVisible();
  await expect(contents.locator("strong")).toHaveText("carefully");
  await expect(contents.getByRole("listitem")).toHaveCount(2);
  await expect(contents.locator("pre")).toContainText("never executed");
  await expect(contents.locator("script, img, iframe, button:not(.task-file-raw-toggle)")).toHaveCount(0);
  await expect(popup.evaluate(() => "documentExecuted" in window)).resolves.toBe(false);
  await reader.state("03-rendered-document-and-inert-commands", { visible: [contents], hidden: [popup.getByRole("alert")] });
  expect(await popup.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await popup.getByRole("button", { name: "Raw", exact: true }).click();
  expect(await popup.locator(".task-file-raw").textContent()).toBe(source);
  await reader.state("04-exact-raw-source", { visible: [popup.locator(".task-file-raw")], hidden: [contents.getByRole("heading")] });
  await popup.getByRole("button", { name: "Raw", exact: true }).click();
  await expect(popup.getByRole("button", { name: "Raw", exact: true })).toHaveAttribute("aria-pressed", "false");
  await popup.evaluate(() => Object.defineProperty(navigator.clipboard, "writeText", { configurable: true, value: async () => { throw new DOMException("Clipboard denied", "NotAllowedError"); } }));
  await popup.getByRole("button", { name: "Copy path" }).click();
  await reader.state("05-copy-denied-manual-path", { visible: [popup.getByText("Could not copy. Select the full path above to copy it manually."), popup.getByLabel("Referenced path")], hidden: [popup.getByText("Path copied.")] });
  await popup.reload();
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await popup.getByRole("button", { name: "Copy path" }).click();
  await expect.poll(() => popup.evaluate(() => navigator.clipboard.readText())).toBe(`file://${paths["commands.md"]}`);
  await reader.state("06-full-path-copied", { visible: [popup.getByText("Path copied.")], hidden: [popup.getByText("Could not copy. Select the full path above to copy it manually.")] });
  await popup.close();
  await expect(page).toHaveURL(`${service}/projects/alpha/tasks/${slug}`);
  await expect(draft).toHaveValue("Keep my unsent follow-up.");
  expect(await page.evaluate(() => ({ top: window.scrollY, regions: [...document.querySelectorAll(".conversation-scroll, .convo-scroll")].map((element) => element.scrollTop) }))).toEqual(scroll);
  await walk.state("07-return-preserves-reading-position", { visible: [link], hidden: [] });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("real file loading, missing recovery, denial, unsupported and empty states", async ({ page, request, service }, info) => {
  const { paths } = await (await request.get(`${service}/fixture/files`)).json();
  const walk = walkthrough(page, info);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/files/alpha?*", async (route) => { await gate; await route.continue(); }, { times: 1 });
  await walk.open(`${service}${fileRoute(paths["commands.md"])}`);
  await walk.state("01-loading-overlay", { visible: [page.getByText("Loading file…"), page.getByLabel("Referenced path")], hidden: [page.getByRole("region", { name: "File contents" })] });
  release();
  await walk.state("02-loaded-document", { visible: [page.getByRole("heading", { name: "Setup instructions" })], hidden: [page.getByText("Loading file…")] });
  await walk.open(`${service}${fileRoute(paths["missing.md"])}`);
  await walk.state("03-real-missing-target", { visible: [page.getByRole("alert"), page.getByRole("button", { name: "Retry" })], hidden: [page.getByRole("region", { name: "File contents" })] });
  expect((await request.post(`${service}/fixture/create-missing`)).ok()).toBe(true);
  await page.getByRole("button", { name: "Retry" }).click();
  await walk.state("04-retry-recovers-real-file", { visible: [page.getByRole("heading", { name: "Recovered document" })], hidden: [page.getByRole("alert"), page.getByRole("button", { name: "Retry" })] });
  for (const [name, status] of [["outside.txt", 403], ["image.png", 415], ["binary.md", 415], ["large.md", 415], ["inaccessible.md", 404]] as const) {
    const response = page.waitForResponse((response) => response.url().includes("/api/files/alpha?"));
    await walk.open(`${service}${fileRoute(paths[name])}`);
    expect((await response).status()).toBe(status);
    await expect(page.getByLabel("Referenced path")).toHaveText(paths[name]);
    await walk.state(`05-unavailable-${name}`, { visible: [page.getByRole("alert")], hidden: [page.getByRole("region", { name: "File contents" })] });
  }
  await walk.open(`${service}${fileRoute(paths["notes.txt"])}`);
  await expect(page.locator(".task-file-raw")).toHaveText("# Literal text\n<b>Keep the brackets.</b>\n");
  await walk.state("06-literal-text-document", { visible: [page.locator(".task-file-raw")], hidden: [page.getByRole("button", { name: "Raw" }), page.getByRole("heading", { name: "Literal text" })] });
  await walk.open(`${service}${fileRoute(paths["empty.md"])}`);
  await walk.state("07-empty-document", { visible: [page.getByText("This file is empty.")], hidden: [page.locator(".task-file-raw"), page.getByRole("alert")] });
});

test("file references remain usable alongside listening, cancellation and microphone denial", async ({ page, request, service }, info) => {
  // Named browser capability overlay: real MediaRecorder gets a synthetic tone, never a device.
  await page.addInitScript(`
    const audio = new AudioContext();
    const tone = audio.createOscillator();
    tone.start();
    window.fixtureDenied = false;
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", { configurable: true, value: async () => {
      if (window.fixtureDenied) throw new DOMException("Fixture denial", "NotAllowedError");
      await audio.resume();
      const target = audio.createMediaStreamDestination();
      tone.connect(target);
      return target.stream;
    }});
  `);
  const { paths, slug } = await (await request.get(`${service}/fixture/files`)).json();
  const walk = walkthrough(page, info);
  await walk.open(`${service}/projects/alpha/tasks/${slug}`);
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = conversation.getByRole("textbox");
  await field.fill("Preserve this draft.");
  const mic = conversation.getByRole("button", { name: "Start voice input" });
  const stop = conversation.getByRole("button", { name: "Stop voice input" });
  const wave = conversation.locator(".composer-wave");
  const link = conversation.getByRole("link", { name: "setup guide" });
  await mic.click();
  await walk.state("01-listening-overlay", { visible: [stop, wave], hidden: [mic] });
  await expect(link).toHaveAttribute("title", paths["commands.md"]);
  await conversation.getByRole("button", { name: "Cancel voice input" }).click();
  await expect(field).toHaveValue("Preserve this draft.");
  await walk.state("02-cancel-clears-listening", { visible: [mic, field], hidden: [stop, wave] });
  await page.evaluate("window.fixtureDenied = true");
  await mic.click();
  await walk.state("03-microphone-denied-overlay", { visible: [conversation.getByText("Microphone blocked in the browser. Typing works."), field], hidden: [stop, wave] });
  await expect(field).toBeEditable();
  const popupPromise = page.waitForEvent("popup");
  await link.click();
  const popup = await popupPromise;
  await expect(popup.getByRole("heading", { name: "Setup instructions" })).toBeVisible();
  await popup.close();
  await expect(field).toHaveValue("Preserve this draft.");
});
