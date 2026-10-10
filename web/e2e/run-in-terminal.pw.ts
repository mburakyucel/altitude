import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Chat commands (SPEC.md §3.3) against real shells: a `run` block in the task conversation and in project
 * chat opens that conversation's terminal with the command typed at the prompt and nothing run until
 * Enter. Walked at both widths: the command block, Copy and Copied, typed at the prompt, Enter (the phone's
 * Enter key) runs it, a
 * program in the foreground, output that never settles, the terminal off, a reload typing nothing, no
 * terminal for a finished task, the project terminal, a plain code block, an unsafe block and a refused
 * copy. The saved messages and the shell's profile are the only fixtures.
 */
test.use({ serviceScript: "terminal-service.py" });

const TASK = "/projects/atlas/tasks/prepare-index-migration";

async function terminalAccess(request: APIRequestContext, enabled: boolean) {
  expect((await request.post("/api/terminal-access", { data: { enabled } })).ok()).toBe(true);
}

async function settled(page: Page) {
  // Longer than the page's settle time: anything it would type has arrived.
  await page.waitForTimeout(1_000);
}

test("a task's chat command opens its terminal typed, and runs only on Enter", { tag: "@chromium" }, async ({ page, request, context }, info) => {
  test.setTimeout(90_000);
  const phone = info.project.name === "phone";
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await terminalAccess(request, true);
  const walk = walkthrough(page, info);
  const block = page.getByRole("group", { name: "Command" });
  const openIt = block.getByRole("button", { name: "Open in terminal" });
  const panel = page.getByRole("region", { name: "Terminal" });
  const output = page.locator(".terminal-screen .xterm-rows");
  const conversation = async () => {
    if (phone) await page.getByRole("navigation", { name: "Task views" }).getByRole("link", { name: "Conversation", exact: true }).click();
  };

  await walk.open(TASK);
  await walk.state("01-command-block", {
    visible: [block.locator("pre").getByText("echo ran-$((20+22))", { exact: true }), block.getByRole("button", { name: "Copy" }), openIt],
    hidden: [panel.locator(".terminal-screen")],
  });

  await block.getByRole("button", { name: "Copy" }).click();
  await walk.state("02-copied", { visible: [block.getByText("Copied")], hidden: [] });
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("echo ran-$((20+22))");

  await openIt.click();
  await expect(page).toHaveURL(new RegExp(`${TASK}/terminal$`));
  await expect(output).toContainText("$ echo ran-$((20+22))");
  await settled(page);
  await walk.state("03-typed-at-prompt", {
    visible: [output.getByText("echo ran-$((20+22))", { exact: false })],
    hidden: [output.getByText("ran-42", { exact: true })],
  });

  // On phone the key row's Enter runs it without focusing the screen, so the soft keyboard stays closed;
  // desktop has no key row, and the screen already has focus for the keyboard's Enter.
  const enter = page.getByRole("toolbar", { name: "Terminal keys" }).getByRole("button", { name: "Enter" });
  if (phone) {
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await enter.click();
    expect(await page.evaluate(() => !!document.activeElement?.closest(".terminal-screen"))).toBe(false);
  } else {
    await expect(enter).toBeHidden();
    await page.keyboard.press("Enter");
  }
  await walk.state("04-enter-runs", { visible: [output.getByText("ran-42", { exact: true })], hidden: [] });
  // Once the shell is back at its prompt, the task's owner is told the command it handed over has run.
  await expect.poll(async () => (await (await request.post("/fixture/notices")).json()).notices, { timeout: 10_000 })
    .toEqual([expect.stringContaining("looks finished in the task terminal: `echo ran-$((20+22))`")]);

  // A program in the foreground would receive the keystrokes, so nothing is typed.
  await page.locator(".terminal-screen").click();
  await page.keyboard.type("sleep 300");
  await page.keyboard.press("Enter");
  await conversation();
  await openIt.click();
  const held = panel.getByRole("status").filter({ hasText: "sleep is running, so the command wasn't typed." });
  await walk.state("05-program-running", {
    visible: [held, held.getByRole("button", { name: "Copy command" }), held.getByRole("button", { name: "Dismiss" })],
    hidden: [],
  });
  await held.getByRole("button", { name: "Dismiss" }).click();
  await expect(held).toBeHidden();
  await page.locator(".terminal-screen").click();
  await page.keyboard.press("Control+C");

  // Output that never settles has no prompt to type at: refused after five seconds, typed later by nothing.
  // It prints far faster than the page's 300 ms settle time, so a loaded runner never opens a quiet gap in which
  // the page checks the terminal and reports bash as the running program instead.
  await page.keyboard.type("bash -c 'while :; do echo tick; sleep 0.02; done'");
  await page.keyboard.press("Enter");
  await conversation();
  await openIt.click();
  const printing = panel.getByRole("status").filter({ hasText: "The terminal kept printing, so the command wasn't typed." });
  await expect(printing).toBeVisible({ timeout: 10_000 });
  await walk.state("05b-kept-printing", { visible: [printing, printing.getByRole("button", { name: "Copy command" })], hidden: [] });
  await printing.getByRole("button", { name: "Dismiss" }).click();
  await page.locator(".terminal-screen").click();
  // The next chat command needs the shell's prompt. On a loaded runner an interrupt pressed into a screen that is
  // still scrolling can miss the shell, so it is pressed again until the prompt is back (an extra one only reprompts).
  const lastRow = async () => (await output.locator(":scope > div").allInnerTexts()).map((row) => row.trimEnd())
    .filter(Boolean).at(-1);
  await expect(async () => {
    await page.keyboard.press("Control+C");
    await expect.poll(lastRow, { timeout: 2_000 }).toMatch(/prepare-index-migration \$$/);
  }).toPass({ timeout: 20_000 });
  await settled(page);

  // When Altitude can't tell the owner, the command is still typed and the operator is asked to reply instead.
  await page.route("**/api/terminal/*/command", (route) => route.fulfill({
    status: 503, contentType: "application/json", body: JSON.stringify({ error: "Altitude is unreachable." }),
  }));
  await conversation();
  await openIt.click();
  const untold = panel.getByRole("status").filter({ hasText: "couldn't tell the task's owner to watch this command, so reply in chat once it has run." });
  await expect(untold).toBeVisible({ timeout: 10_000 });
  await expect(output).toContainText(/\^C\s*prepare-index-migration \$ echo ran-\$\(\(20\+22\)\)/);
  await walk.state("05c-owner-not-told", { visible: [untold, untold.getByRole("button", { name: "Dismiss" })], hidden: [] });
  await page.unroute("**/api/terminal/*/command");
  await untold.getByRole("button", { name: "Dismiss" }).click();
  await expect(untold).toBeHidden();
  await page.locator(".terminal-screen").click();
  await page.keyboard.press("Control+C");
  await page.keyboard.type("clear");
  await page.keyboard.press("Enter");
  await settled(page);
  await expect(output).not.toContainText("echo ran-");
  await page.keyboard.type("exit");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`${TASK}/live$`));

  // Off: the card, and the request is dropped; a reload after turning it on types nothing.
  await terminalAccess(request, false);
  await page.reload();
  await conversation();
  await openIt.click();
  await walk.state("06-terminal-off", { visible: [panel.getByText("Terminal is off")], hidden: [panel.locator(".terminal-screen")] });
  await terminalAccess(request, true);
  await page.reload();
  await expect(output).toContainText("$");
  await settled(page);
  await walk.state("07-reload-types-nothing", { visible: [output], hidden: [output.getByText("echo ran-", { exact: false })] });

  await request.post("/fixture/finish");
  await expect(page).toHaveURL(new RegExp(`${TASK}/live$`));
  await conversation();
  await expect(openIt).toHaveAttribute("aria-disabled", "true");
  await expect(openIt).toHaveAccessibleDescription("This task has no terminal now.");
  // aria-disabled keeps the native button pressable for its reason; Playwright needs an explicit press.
  await openIt.click({ force: true });
  await walk.state("08-no-terminal-here", {
    visible: [openIt, block.getByText("This task has no terminal now."), block.getByRole("button", { name: "Copy" })],
    hidden: [],
  });
});

test("a project chat command opens the project terminal; other blocks copy only", { tag: "@chromium" }, async ({ page, request, context }, info) => {
  test.setTimeout(60_000);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await terminalAccess(request, true);
  const walk = walkthrough(page, info);
  const chat = page.getByRole("region", { name: "Conversation", exact: true });
  const command = chat.getByRole("group", { name: "Command" });
  const output = page.locator(".terminal-screen .xterm-rows");
  const plain = chat.locator(".code-block");

  await walk.open("/projects/atlas");
  await walk.state("11-project-blocks", {
    visible: [
      command.first().getByRole("button", { name: "Open in terminal" }),
      plain.getByText("uname -a"), plain.getByRole("button", { name: "Copy" }),
      command.last().getByRole("button", { name: "Open in terminal" }),
    ],
    hidden: [plain.getByRole("button", { name: "Open in terminal" })],
  });
  const unavailable = command.last().getByRole("button", { name: "Open in terminal" });
  await expect(unavailable).toHaveAttribute("aria-disabled", "true");
  await expect(unavailable).toHaveAccessibleDescription("Not offered for the terminal: more than one line.");
  await unavailable.focus();
  await page.keyboard.press("Enter");
  await walk.state("11b-unavailable-reason", { visible: [command.last().getByText("Not offered for the terminal: more than one line.")], hidden: [output] });

  await page.evaluate(() => { navigator.clipboard.writeText = () => Promise.reject(new Error("refused")); });
  await plain.getByRole("button", { name: "Copy" }).click();
  await walk.state("12-copy-refused", { visible: [plain.getByText("Couldn't copy")], hidden: [] });

  await command.first().getByRole("button", { name: "Open in terminal" }).click();
  await expect(page).toHaveURL(/\/projects\/atlas\/terminal$/);
  await expect(output).toContainText("$ echo project-$((5*5))");
  await settled(page);
  await walk.state("13-project-typed", {
    visible: [output.getByText("echo project-$((5*5))", { exact: false })],
    hidden: [output.getByText("project-25", { exact: true })],
  });
  await page.keyboard.press("Enter");
  await walk.state("14-project-enter-runs", {
    visible: [output.getByText("project-25", { exact: true }), page.getByText("The coordinator can read this terminal's output, and what it reads reaches its AI provider.")],
    hidden: [],
  });
  // Once the shell is back at its prompt, the coordinator is told the command it handed over has run.
  await expect.poll(async () => (await (await request.post("/fixture/notices")).json()).coordinator, { timeout: 10_000 })
    .toEqual([expect.stringContaining("looks finished in the project terminal: `echo project-$((5*5))`")]);
});
