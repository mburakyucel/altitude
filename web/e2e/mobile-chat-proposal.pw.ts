import { createServer, type Server } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { test, expect } from "@playwright/test";
import { walkthrough } from "./walkthrough";

// Approved design examples. No application API, task transition, or provider execution.
let server: Server;
let origin: string;
const root = resolve("..");
test.beforeAll(async () => {
  server = createServer(async (req, res) => {
    const path = resolve(root, "." + new URL(req.url!, "http://localhost").pathname);
    try {
      if (!path.startsWith(root + sep)) throw new Error("Outside fixture tree");
      const body = await readFile(path);
      res.setHeader("Content-Type", ({ ".html": "text/html", ".css": "text/css", ".js": "text/javascript" })[extname(path)] || "text/plain");
      res.end(body);
    } catch { res.writeHead(404); res.end("Not found"); }
  });
  await new Promise<void>(done => server.listen(0, "127.0.0.1", done));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Missing fixture port");
  origin = `http://127.0.0.1:${address.port}/design/wireframes/`;
});
test.afterAll(async () => { await new Promise<void>((done, reject) => server.close(error => error ? reject(error) : done())); });

const cases = [
  ["task-held", "Running", true],
  ["task-blocked", "Waits for L3", false],
  ["task-blocked-held", "Waits for L3", true],
  ["task-question-held", "Needs your answer", true],
  ["task-fault-held", "Blocked by a fault", true],
  ["task-paused", "Paused", false],
] as const;

for (const [scene, status, held] of cases) {
  test(`mobile chat proposal: ${scene} collapsed, expanded and restored`, async ({ page }, info) => {
    const phone = info.project.name === "phone";
    const walk = walkthrough(page, info);
    const errors: string[] = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.route("https://fonts.**/*", route => route.abort());
    const dialog = page.getByRole("dialog");
    const field = page.getByRole("textbox", { name: "Message the L2" });
    const trigger = page.getByRole("button", { name: "Details and actions" });
    const heading = page.locator(".identity small");
    const openQuestion = ["task-blocked", "task-blocked-held", "task-question-held"].includes(scene);
    for (const keyboard of phone ? [false, true] : [false]) {
      const suffix = keyboard ? "keyboard" : "reading";
      await walk.open(`${origin}${phone ? "MobileChatProposal" : "TaskStatusProposal"}.html?scene=${scene}${keyboard ? "&keyboard" : ""}`);
      await expect(heading).toContainText(status);
      if (held) await expect(heading).toContainText("Merge held");
      await walk.state(`${scene}-${suffix}-collapsed`, { visible: [heading, field, trigger], hidden: [dialog] });
      await walk.state(`${scene}-${suffix}-expanded`, { action: () => trigger.click(), visible: [dialog, dialog.getByRole("heading", { name: "Task details", exact: true })], hidden: [] });
      if (held) await expect(dialog.getByText(/Design approval alone does not release this hold/)).toBeVisible();
      if (scene.includes("blocked")) await expect(dialog.getByText(/including retries after a saved cursor expires/)).toBeVisible();
      if (held) {
        const reason = dialog.locator(".merge-reason .reason");
        await walk.state(`${scene}-${suffix}-full-merge-reason`, { action: () => reason.scrollIntoViewIfNeeded(), visible: [reason], hidden: [dialog.getByRole("button", { name: /release.*hold|merge now/i })] });
        await expect(reason).toBeInViewport();
      }
      if (openQuestion) await expect(dialog.getByRole("button", { name: "Resume", exact: true })).toHaveCount(0);
      else if (scene !== "task-held") await expect(dialog.getByRole("button", { name: "Resume", exact: true })).toBeVisible();
      const box = await dialog.boundingBox();
      expect(box!.y + box!.height).toBeLessThanOrEqual(phone ? keyboard ? 510 : 844 : 900);
      expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
      await walk.state(`${scene}-${suffix}-collapsed-again`, { action: () => page.keyboard.press("Escape"), visible: [heading, field], hidden: [dialog] });
      await expect(trigger).toBeFocused();
      if (scene === "task-question-held") {
        await trigger.click();
        await dialog.getByRole("button", { name: "View question", exact: true }).click();
        await expect(dialog).toBeHidden();
        await expect(page.getByRole("article", { name: "Pending question" })).toBeFocused();
        await expect(heading).toContainText("Needs your answer · Merge held");
        await page.locator(".messages").evaluate(el => { el.scrollTop = el.scrollHeight; });
      }
      if (keyboard && openQuestion) {
        await walk.state(`${scene}-question-anchor`, { action: () => page.getByRole("button", { name: "View question", exact: true }).click(), visible: [page.getByRole("article", { name: "Pending question" }), page.getByRole("button", { name: "Latest messages", exact: true })], hidden: [page.getByRole("button", { name: "View question", exact: true })] });
        await expect(page.getByRole("article", { name: "Pending question" })).toBeFocused();
      }
      if (keyboard) {
        await field.fill("Keep this as an unsent follow-up.");
        await field.evaluate(el => (el as HTMLTextAreaElement).setSelectionRange(5, 9));
        await walk.state(`${scene}-keyboard-restored`, { action: () => page.getByRole("button", { name: "Dismiss keyboard" }).click(), visible: [page.getByRole("navigation", { name: "Main navigation" }), field], hidden: [page.locator(".keyboard")] });
        await expect(field).toHaveValue("Keep this as an unsent follow-up.");
        await expect(field).toBeFocused();
        expect(await field.evaluate(el => [(el as HTMLTextAreaElement).selectionStart, (el as HTMLTextAreaElement).selectionEnd])).toEqual([5, 9]);
        if (held) await expect(heading).toContainText("Merge held");
      }
      if (scene === "task-fault-held") await expect(page.locator(".fault-notice")).toContainText("Verification browser could not start.");
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(phone ? 390 : 1440);
    }
    expect(errors).toEqual([]);
  });
}
