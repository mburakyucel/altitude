import { createServer, type Server } from "node:http";
import { readFile, mkdir, copyFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { test, expect } from "@playwright/test";
import { walkthrough } from "./walkthrough";

// Static fictional design evidence; no application API, operator records or real provider execution.
let server: Server;
let origin: string;
const root = resolve("..");
test.beforeAll(async () => {
  server = createServer(async (req, res) => {
    const path = resolve(root, "." + new URL(req.url!, "http://localhost").pathname);
    try {
      if (!path.startsWith(root + sep)) throw new Error("Outside fixture tree");
      const body = await readFile(path);
      res.setHeader("Content-Type", ({ ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png" })[extname(path)] || "text/plain");
      res.end(body);
    } catch { res.writeHead(404); res.end("Not found"); }
  });
  await new Promise<void>(done => server.listen(0, "127.0.0.1", done));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Missing fixture port");
  origin = `http://127.0.0.1:${address.port}/design/wireframes/l2-progress/`;
});
test.afterAll(async () => { await new Promise<void>((done, reject) => server.close(error => error ? reject(error) : done())); });

test("L2 progress proposal: steer, stop and continue with draft and conversation intact", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const draft = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const stop = page.getByRole("button", { name: /^Stop(?: Esc)?$/, exact: true });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const observe = (state: string) => page.evaluate(state => (window as unknown as { observe: (value: string) => void }).observe(state), state);
  await walk.open(origin + "board.html");
  await walk.state("running", { visible: [page.getByText("Latest from L2", { exact: true }), page.getByRole("region", { name: "L2 activity" }).getByText("Tool output observed · 8 sec ago", { exact: true }), stop, draft], hidden: [page.getByText("Queued · waiting for a checkpoint", { exact: true })] });
  await draft.fill("Check the old cursor format before editing.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await walk.state("queued", { visible: [page.getByText("Queued · waiting for a checkpoint", { exact: true }), stop], hidden: [page.getByText("Delivered to session · 10:42", { exact: true })] });
  await expect(page.getByText("Queued · waiting for a checkpoint", { exact: true })).toBeInViewport({ ratio: 1 });
  await expect(draft).toHaveValue("");
  await observe("received");
  await walk.state("received", { visible: [page.getByText("Delivered to session · 10:42", { exact: true })], hidden: [page.getByText("Queued · waiting for a checkpoint", { exact: true })] });
  await draft.fill("Keep the edits; check cursor decoding first.");
  await draft.evaluate((element: HTMLTextAreaElement) => element.setSelectionRange(5, 14));
  await page.getByRole("button", { name: /View live session/ }).click();
  await walk.state("live-session", { visible: [page.getByRole("complementary", { name: "Live session" }), page.getByRole("button", { name: "Stop worker", exact: true })], hidden: info.project.name === "phone" ? [draft] : [] });
  await page.getByRole("button", { name: "Return to conversation", exact: true }).click();
  await expect(draft).toHaveValue("Keep the edits; check cursor decoding first.");
  expect(await draft.evaluate((element: HTMLTextAreaElement) => [element.selectionStart, element.selectionEnd])).toEqual([5, 14]);
  await stop.click();
  await walk.state("stopping", { visible: [page.getByText("Waiting for the worker to end", { exact: true }), draft], hidden: [page.getByRole("button", { name: "Continue session", exact: true })] });
  await expect(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
  await draft.fill("Check the old cursor format first.");
  await observe("stopped");
  await walk.state("stopped", { visible: [page.getByText("Your edits and this session are kept.", { exact: true }), page.getByRole("button", { name: "Continue session", exact: true }), draft], hidden: [stop] });
  await expect(draft).toHaveValue("Check the old cursor format first.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await walk.state("waiting-to-resume", { visible: [page.getByText("Waiting for a worker slot", { exact: true }), page.getByText("Queued · waiting to resume", { exact: true })], hidden: [page.getByRole("button", { name: "Continue session", exact: true })] });
  await observe("received");
  await observe("resumed");
  await walk.state("resumed", { visible: [page.getByText("I’ll check the old cursor format first, keeping the existing edits.", { exact: true }), stop], hidden: [page.getByText("Waiting for a worker slot", { exact: true })] });
  await expect(page.locator(".bubble").filter({ hasText: "Check the old cursor format before editing." })).toHaveCount(1);
  await expect(page.locator(".bubble").filter({ hasText: "Check the old cursor format first." })).toHaveCount(1);
  expect(errors).toEqual([]);
  // Deliberate opt-in capture generation, not a normal test write into tracked source.
  if (process.env.CAPTURE_L2_PROPOSAL === "1") {
    const out = resolve(root, "design/wireframes/l2-progress/captures");
    await mkdir(out, { recursive: true });
    for (const scene of ["running", "queued", "stopped", "resumed"]) await copyFile(info.outputPath(`${scene}.png`), resolve(out, `${info.project.name}-${scene}.png`));
  }
});

test("L2 progress proposal: quiet, missing, blocked, finished and input ownership", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const draft = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const scene = async (state: string, text: string) => {
    await walk.open(origin + "board.html?state=" + state);
    await walk.state(state, { visible: [page.getByText(text, { exact: true })], hidden: [] });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  };
  await scene("loading", "Waiting for the session records.");
  await scene("no-commentary", "No public update yet.");
  await scene("quiet", "No new activity for 4 min");
  await scene("unavailable", "Activity unavailable");
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByText("Activity unavailable", { exact: true })).toBeHidden();
  await scene("stop-error", "Stop unconfirmed");
  await expect(draft).toHaveValue("Check the old cursor format first.");
  await expect(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Check status", exact: true }).click();
  await expect(page.getByText("Waiting for the worker to end", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue session", exact: true })).toBeHidden();
  await scene("denied", "Action denied. Your draft is kept.");
  await scene("refused", "Not sent. Retry.");
  await scene("unconfirmed", "Delivery unconfirmed");
  await walk.open(origin + "board.html?state=stopped");
  await draft.fill("Keep this unsent draft.");
  await page.getByRole("button", { name: "Continue session", exact: true }).click();
  await walk.state("continue-with-unsent-draft", { visible: [draft, page.getByText("Waiting to continue this saved session.", { exact: true })], hidden: [page.getByRole("button", { name: "Continue session", exact: true })] });
  await expect(draft).toHaveValue("Keep this unsent draft.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Waiting for a worker slot", { exact: true })).toBeVisible();
  await expect(page.locator("#task-state")).toHaveText("Waiting to resume");
  await scene("blocked", "How long should old pagination tokens keep working?");
  await expect(page.getByRole("region", { name: "L2 activity" })).toBeHidden();
  await scene("finished", "The compatibility change is ready. Existing clients keep working and the checks passed.");
  await expect(draft).toBeHidden();
  await scene("mic-denied", "Microphone blocked in the browser. Typing works.");
  await expect(page.getByRole("button", { name: "Use microphone", exact: true })).toBeDisabled();
  await walk.open(origin + "board.html?state=voice-unavailable");
  await walk.state("voice-unavailable", { visible: [draft], hidden: [page.getByRole("button", { name: "Use microphone", exact: true })] });
  await walk.open(origin + "board.html");
  await draft.fill("Keep this draft.");
  await page.getByRole("button", { name: "Use microphone", exact: true }).click();
  await walk.state("listening", { visible: [page.getByText("Listening · 0:08", { exact: true })], hidden: [draft] });
  await page.keyboard.press("Escape");
  await expect(draft).toHaveValue("Keep this draft.");
  await expect(page.getByText("Waiting for the worker to end", { exact: true })).toBeHidden();
  await page.getByRole("button", { name: "Task details", exact: true }).click();
  await page.getByRole("button", { name: "Reject task…", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.keyboard.press("Escape");
  await expect(page.getByText("Task details", { exact: true })).toBeHidden();
  await draft.focus();
  await page.keyboard.press("Escape");
  await expect(page.getByText("Waiting for the worker to end", { exact: true })).toBeHidden();
  if (info.project.name === "desktop") {
    await draft.evaluate((element: HTMLTextAreaElement) => element.blur());
    await page.keyboard.press("Escape");
    await expect(page.getByText("Waiting for the worker to end", { exact: true })).toBeVisible();
  }
  await walk.open(origin + "board.html?compact");
  await walk.state("pending-compact-header", { visible: [draft], hidden: [] });
  await page.setViewportSize({ width: info.project.name === "phone" ? 390 : 1440, height: 510 });
  await walk.state("short-viewport", { visible: [draft, page.getByRole("button", { name: /View live session/ })], hidden: [page.locator("#note")] });
  await page.getByRole("button", { name: "Expand latest update", exact: true }).click();
  await walk.state("short-viewport-expanded", { visible: [draft, page.locator("#note")], hidden: [] });
});
