import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * The operator's terminal (SPEC.md §3.10) against real shells on real pseudo-terminals. Every state is
 * walked at both widths: off, the Settings switch, running, keys and tab completion, copy and paste, the
 * phone's Enter key at a prompt and a hidden password prompt, a
 * running command's close confirmation, reconnecting, typing stopped after failed input, a non-zero and a
 * clean exit, task finished, restart pending, refused, closed elsewhere, Altitude restarted and Close.
 * Only the agent check and the restart notice are fixtures.
 */
test.use({ serviceScript: "terminal-service.py" });

const TASK = "/projects/atlas/tasks/prepare-index-migration";

async function fixture(request: APIRequestContext, path: string, data: object = {}) {
  const response = await request.post(`/fixture/${path}`, { data });
  expect(response.ok()).toBe(true);
}

/** Type a command line into the terminal the page shows, once its prompt is drawn. */
async function run(page: Page, line: string) {
  await expect(page.locator(".terminal-screen .xterm-rows")).toContainText("$");
  await page.locator(".terminal-screen").click();
  await page.keyboard.type(line);
  await page.keyboard.press("Enter");
}

test("a task terminal opens in its worktree, closes when its shell exits and follows the task", { tag: "@chromium" }, async ({ page, request, context }, info) => {
  test.setTimeout(120_000);
  const phone = info.project.name === "phone";
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const walk = walkthrough(page, info);
  const panel = page.getByRole("region", { name: "Terminal" });
  const output = page.locator(".terminal-screen .xterm-rows");
  const keys = page.getByRole("toolbar", { name: "Terminal keys" });
  const toast = page.getByRole("status").filter({ hasText: /Terminal closed|terminal closed/ });
  const views = phone ? page.getByRole("navigation", { name: "Task views" }) : page.getByRole("navigation", { name: "Panel view" });
  const close = panel.getByRole("button", { name: phone ? "Close terminal" : "Close" });

  await walk.open(TASK);
  await views.getByRole("link", { name: "Terminal" }).click();
  await walk.state("01-off", {
    visible: [panel.getByText("Terminal is off"), panel.getByRole("link", { name: "Open Settings" })],
    hidden: [page.locator(".terminal-screen")],
  });

  await panel.getByRole("link", { name: "Open Settings" }).click();
  const toggle = page.getByRole("switch", { name: /Terminal/ });
  await expect(toggle).toBeEnabled();
  await toggle.click();
  await walk.state("02-settings-switch-on", { visible: [toggle, page.getByText(/can run commands as you on this computer/)], hidden: [] });
  await expect(toggle).toBeChecked();
  await page.goBack();

  // Showing the terminal opens it: no Ready step, no banner, one dim line says where it runs.
  await expect(output).toContainText("Runs as you in ");
  await run(page, "echo hello-$((6*7)); basename $PWD");
  await expect(output).toContainText("hello-42");
  await walk.state("03-running", {
    visible: [output.getByText("prepare-index-migration", { exact: false }).last(), close, panel.getByText("This task's owner can read this terminal's output.")],
    hidden: [panel.getByText(/L2's worktree/), panel.getByRole("button", { name: "Open terminal" }), ...(phone ? [] : [keys])],
  });
  if (phone) await expect(keys).toBeVisible();

  // Tab completion, arrows and Escape reach the shell; nothing around the terminal takes them.
  await page.locator(".terminal-screen").click();
  await page.keyboard.type("ech");
  await page.keyboard.press("Tab");
  await page.keyboard.type(" completed-$((1+2))");
  await page.keyboard.press("Enter");
  await expect(output).toContainText("completed-3");
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Control+U");
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+C");
  await expect(panel).toBeVisible();
  // The shell turns bracketed paste back on as its new prompt starts; a paste before that would run at once.
  await expect(output).toHaveText(/\^C\s*prepare-index-migration \$\s*$/);

  // Paste: the phone's Paste key reads the clipboard; on desktop the browser's paste reaches the shell.
  // Both paste as the shell asks (bracketed), so pasted lines wait for Enter.
  await page.evaluate(() => navigator.clipboard.writeText("echo pasted-$((7+7))\necho twice-$((1+1))"));
  if (phone) await keys.getByRole("button", { name: "Paste" }).click();
  else await page.keyboard.press("Control+V");
  await expect(output).toContainText("echo twice-$((1+1))");
  await page.waitForTimeout(500);
  await expect(output).not.toContainText("pasted-14");
  // The phone's Enter key runs the line as the keyboard's Return would, and leaves the soft keyboard closed.
  const enter = keys.getByRole("button", { name: "Enter" });
  const screenFocused = () => page.evaluate(() => !!document.activeElement?.closest(".terminal-screen"));
  if (phone) {
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await enter.click();
    expect(await screenFocused()).toBe(false);
  } else await page.keyboard.press("Enter");
  await expect(output).toContainText("pasted-14");
  await expect(output).toContainText("twice-2");
  if (phone) {
    // A hidden password prompt (as sudo shows) takes its answer with the same key.
    await run(page, "read -rs -p 'Password: ' secret; echo; echo got-${#secret}-$secret");
    await expect(output).toContainText("Password:");
    await page.evaluate(() => navigator.clipboard.writeText("hunter2"));
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await keys.getByRole("button", { name: "Paste" }).click();
    await enter.click();
    await expect(output).toContainText("got-7-hunter2");
    await walk.state("03b-enter-key", { visible: [enter], hidden: [] });
  }
  if (!phone) {
    // Ctrl+C with text selected copies it; the line being typed is not interrupted.
    await page.evaluate(() => navigator.clipboard.writeText(""));
    await page.keyboard.type("echo still-");
    const word = await output.getByText("pasted-14").last().boundingBox();
    await page.mouse.dblclick(word!.x + 12, word!.y + word!.height / 2);
    await page.keyboard.press("Control+C");
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("pasted");
    await page.keyboard.type("here");
    await page.keyboard.press("Enter");
    await expect(output).toContainText("still-here");
  }

  await run(page, "sleep 300");
  await close.click();
  const confirm = panel.getByText("sleep is still running and will be stopped.");
  await walk.state("04-close-running-command", { visible: [panel.getByText("Close the terminal?"), confirm], hidden: [] });
  await panel.getByRole("button", { name: "Cancel" }).click();
  await expect(confirm).toBeHidden();
  // Ctrl+C: the phone's key row sends it as a sticky Ctrl then the letter.
  if (phone) {
    await keys.getByRole("button", { name: "Control" }).click();
    await expect(keys.getByRole("button", { name: "Control" })).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.type("c");
  } else {
    await page.locator(".terminal-screen").click();
    await page.keyboard.press("Control+C");
  }
  await run(page, "echo after-$((1+1))");
  await expect(output).toContainText("after-2");

  // A lost connection shows over the screen, so the shell keeps its size.
  const size = await page.locator(".terminal-screen").boundingBox();
  await fixture(request, "drop");
  const lost = panel.getByText("Reconnecting…");
  await walk.state("05-reconnecting", { visible: [lost], hidden: [] });
  expect(await page.locator(".terminal-screen").boundingBox()).toEqual(size);
  await fixture(request, "back");
  await expect(lost).toBeHidden({ timeout: 10_000 });
  await run(page, "echo back-$((2+2))");
  await expect(output).toContainText("back-4");

  // Input that fails may have arrived in part: typing stops until the operator has checked the screen.
  await page.route("**/api/terminal/atlas/input", (route) => route.fulfill({ status: 409, json: { error: "The terminal is not reading input. Press Ctrl+C or close it." } }), { times: 1 });
  await run(page, "echo lost-$((5+5))");
  const stopped = panel.getByRole("alert").filter({ hasText: "Typing stopped" });
  await walk.state("06-typing-stopped", {
    visible: [stopped, stopped.getByText(/not reading input/), stopped.getByRole("button", { name: "Resume typing" })],
    hidden: [lost],
  });
  await expect(output).not.toContainText("lost-10");
  await stopped.getByRole("button", { name: "Resume typing" }).click();
  await expect(stopped).toBeHidden();
  await run(page, "echo resumed-$((5+6))");
  await expect(output).toContainText("resumed-11");

  // The shell's exit closes the terminal and returns to Live session; a failure code is named.
  await run(page, "exit 3");
  await expect(page).toHaveURL(new RegExp(`${TASK}/live$`));
  await walk.state("07-exit-code", {
    visible: [toast.getByText("Terminal closed · exit code 3")],
    hidden: [panel, keys],
  });

  // Coming back starts a fresh shell; a clean exit returns without a notice.
  await views.getByRole("link", { name: "Terminal" }).click();
  await run(page, "echo fresh-$((3+3))");
  await expect(output).toContainText("fresh-6");
  await expect(output).not.toContainText("back-4");
  await toast.getByRole("button", { name: "Dismiss" }).click();
  await run(page, "exit");
  await expect(page).toHaveURL(new RegExp(`${TASK}/live$`));
  await walk.state("08-exit-clean", { visible: [views.getByRole("link", { name: "Terminal" })], hidden: [panel, toast] });

  await views.getByRole("link", { name: "Terminal" }).click();
  await expect(output).toContainText("$");
  await fixture(request, "finish");
  await expect(page).toHaveURL(new RegExp(`${TASK}/live$`));
  await walk.state("09-task-finished", {
    visible: [toast.getByText("The task finished, so its terminal closed.")],
    hidden: [panel, views.getByRole("link", { name: "Terminal" })],
  });
});

test("a project terminal opens in the project folder, refuses agents and ends with a restart", async ({ page, request }, info) => {
  test.setTimeout(90_000);
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const panel = page.getByRole("region", { name: "Terminal" });
  const output = page.locator(".terminal-screen .xterm-rows");
  const toast = page.getByRole("status").filter({ hasText: /terminal/ });
  const close = panel.getByRole("button", { name: "Close", exact: true });
  // The restart notice is presentation: the real overview with a pending activation laid over it.
  await page.route("**/api/overview", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), restart: { since: "2026-09-24T10:00:00Z", head: "fixture", files: [], waiting_for: [] } } });
  });
  expect((await request.post("/api/terminal-access", { data: { enabled: true } })).ok()).toBe(true);
  // Below the inline panel width the desktop terminal is an overlay, which must leave Escape and Tab to the shell.
  if (!phone) await page.setViewportSize({ width: 1100, height: 900 });

  await fixture(request, "agent", { on: true });
  await walk.open("/projects/atlas");
  await page.getByRole("button", { name: "Terminal", exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/atlas\/terminal$/);
  const refused = panel.getByText("Terminal requests from Altitude's own agents are refused.");
  await walk.state("11-refused", { visible: [panel.getByText("Couldn't read the terminal"), refused], hidden: [page.locator(".terminal-screen")] });
  if (phone) await expect(page.getByText("atlas · project folder")).toBeVisible();
  await fixture(request, "agent", { on: false });
  await panel.getByRole("button", { name: "Retry" }).click();
  await expect(output).toContainText("Runs as you in ");
  await run(page, "basename $PWD");
  await expect(output).toContainText("atlas");
  await walk.state("12-project-running-restart-pending", {
    visible: [panel.getByText(/Altitude restarts at its next quiet point/), output.getByText("atlas").last(), close],
    hidden: [refused, panel.getByText(/clean main/), panel.getByText(/task's owner can read/)],
  });
  await page.locator(".terminal-screen").click();
  await page.keyboard.type("ech");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Escape");
  await page.keyboard.type(" kept-$((8+1))");
  await page.keyboard.press("Enter");
  await expect(output).toContainText("kept-9");
  await expect(panel).toBeVisible();

  // Another tab or device closes it: this page leaves and says so.
  const opened = await (await request.get("/api/terminal/atlas")).json();
  expect((await request.post("/api/terminal/atlas/close", { data: { id: opened.id } })).ok()).toBe(true);
  await expect(page).toHaveURL(/\/projects\/atlas$/);
  await walk.state("13-closed-elsewhere", { visible: [toast.getByText("The terminal was closed elsewhere.")], hidden: [panel] });

  await page.getByRole("button", { name: "Terminal", exact: true }).click();
  await run(page, "echo again-$((4+4))");
  await expect(output).toContainText("again-8");
  await fixture(request, "restart");
  await expect(page).toHaveURL(/\/projects\/atlas$/);
  await walk.state("14-altitude-restarted", {
    visible: [toast.getByText("The terminal closed while the connection was lost.")],
    hidden: [panel],
  });

  await page.getByRole("button", { name: "Terminal", exact: true }).click();
  await run(page, "echo last-$((4+5))");
  await expect(output).toContainText("last-9");
  await toast.getByRole("button", { name: "Dismiss" }).click();
  await close.click();
  await expect(page).toHaveURL(/\/projects\/atlas$/);
  await walk.state("15-closed-back-to-project", { visible: [page.getByRole("button", { name: "Terminal", exact: true })], hidden: [panel, toast] });
  // An overview read still in flight at teardown would find its response disposed.
  await page.unrouteAll({ behavior: "ignoreErrors" });
});
