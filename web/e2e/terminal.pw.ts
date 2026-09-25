import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * The operator's terminal (SPEC.md §3.10) against real shells on real pseudo-terminals. Every state on the
 * approved board is walked at both widths: off, the Settings switch, ready, running, a running command's
 * close confirmation, reconnecting, typing stopped after failed input, shell exited, task finished, restart pending, refused, could not
 * start and Altitude restarted. Only the agent check and the restart notice are fixtures.
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

test("a task terminal opens in its worktree and follows the task's lifecycle", async ({ page, request }, info) => {
  test.setTimeout(90_000);
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const panel = page.getByRole("region", { name: "Terminal" });
  const output = page.locator(".terminal-screen .xterm-rows");
  const keys = page.getByRole("toolbar", { name: "Terminal keys" });

  await walk.open(TASK);
  const tab = phone ? page.getByRole("navigation", { name: "Task views" }).getByRole("link", { name: "Terminal" })
    : page.getByRole("navigation", { name: "Panel view" }).getByRole("link", { name: "Terminal" });
  await tab.click();
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

  const open = panel.getByRole("button", { name: "Open terminal" });
  await walk.state("03-ready", {
    visible: [panel.getByText("Open a terminal in this task's worktree"), open, panel.getByText(/This is the L2's worktree/)],
    hidden: [page.locator(".terminal-screen")],
  });

  await open.click();
  await run(page, "echo hello-$((6*7)); basename $PWD");
  await expect(output).toContainText("hello-42");
  await walk.state("04-running", {
    visible: [output.getByText("prepare-index-migration", { exact: false }).last(), panel.getByRole("button", { name: phone ? "Close" : "Close terminal" })],
    hidden: phone ? [] : [keys],
  });
  if (phone) await expect(keys).toBeVisible();

  await run(page, "sleep 300");
  await panel.getByRole("button", { name: phone ? "Close" : "Close terminal" }).click();
  const confirm = panel.getByText("sleep is still running and will be stopped.");
  await walk.state("05-close-running-command", { visible: [panel.getByText("Close the terminal?"), confirm], hidden: [] });
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

  await fixture(request, "drop");
  const lost = panel.getByText(/Connection lost · reconnecting/);
  await walk.state("06-reconnecting", { visible: [lost], hidden: [] });
  await fixture(request, "back");
  await expect(lost).toBeHidden({ timeout: 10_000 });
  await run(page, "echo back-$((2+2))");
  await expect(output).toContainText("back-4");

  // Input that fails may have arrived in part: typing stops until the operator has checked the screen.
  await page.route("**/api/terminal/atlas/input", (route) => route.fulfill({ status: 409, json: { error: "The terminal is not reading input. Press Ctrl+C or close it." } }), { times: 1 });
  await run(page, "echo lost-$((5+5))");
  const stopped = panel.getByRole("alert").filter({ hasText: "Typing stopped" });
  await walk.state("06b-typing-stopped", {
    visible: [stopped, stopped.getByText(/not reading input/), stopped.getByRole("button", { name: "Resume typing" })],
    hidden: [lost],
  });
  await expect(output).not.toContainText("lost-10");
  await stopped.getByRole("button", { name: "Resume typing" }).click();
  await expect(stopped).toBeHidden();
  await run(page, "echo resumed-$((5+6))");
  await expect(output).toContainText("resumed-11");

  await run(page, "exit 3");
  const exited = panel.getByText("Terminal closed · exit code 3");
  await walk.state("07-shell-exited", {
    visible: [exited, panel.getByRole("button", { name: "Open a new terminal" }), output.getByText("back-4").last()],
    hidden: [panel.getByRole("button", { name: /^Close/ }), keys],
  });

  await panel.getByRole("button", { name: "Open a new terminal" }).click();
  await expect(exited).toBeHidden();
  await run(page, "echo fresh-$((3+3))");
  await expect(output).toContainText("fresh-6");
  await expect(output).not.toContainText("back-4");

  await fixture(request, "finish");
  await walk.state("08-task-finished", {
    visible: [panel.getByText(/This task finished and its worktree was removed/)],
    hidden: [panel.getByRole("button", { name: "Open a new terminal" }), panel.getByRole("button", { name: /^Close/ })],
  });
});

test("a project terminal opens in the project folder, refuses agents and ends with a restart", async ({ page, request }, info) => {
  test.setTimeout(90_000);
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const panel = page.getByRole("region", { name: "Terminal" });
  const output = page.locator(".terminal-screen .xterm-rows");
  // The restart notice is presentation: the real overview with a pending activation laid over it.
  await page.route("**/api/overview", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), restart: { since: "2026-09-24T10:00:00Z", head: "fixture", files: [], waiting_for: [] } } });
  });
  expect((await request.post("/api/terminal-access", { data: { enabled: true } })).ok()).toBe(true);

  await walk.open("/projects/atlas");
  await page.getByRole("button", { name: "Terminal", exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/atlas\/terminal$/);
  const open = panel.getByRole("button", { name: "Open terminal" });
  await walk.state("11-project-ready", {
    visible: [panel.getByText("Open a terminal in the project folder"), open, panel.getByText(/lands PRs from this folder's clean main/)],
    hidden: [page.locator(".terminal-screen")],
  });
  if (phone) await expect(page.getByText("atlas · project folder")).toBeVisible();

  await fixture(request, "agent", { on: true });
  await open.click();
  const refused = panel.getByText("Terminal requests from Altitude's own agents are refused.");
  await walk.state("12-refused", { visible: [panel.getByText("Couldn't open a terminal"), refused], hidden: [page.locator(".terminal-screen")] });
  await fixture(request, "agent", { on: false });
  await panel.getByRole("button", { name: "Retry" }).click();
  await run(page, "basename $PWD");
  await expect(output).toContainText("atlas");
  await walk.state("13-project-running-restart-pending", {
    visible: [panel.getByText(/Altitude restarts at its next quiet point/), output.getByText("atlas").last()],
    hidden: [refused],
  });

  await fixture(request, "restart");
  const restarted = panel.getByText(/Altitude restarted, which ends open terminals/);
  await walk.state("14-altitude-restarted", {
    visible: [restarted, panel.getByRole("button", { name: "Open a new terminal" })],
    hidden: [page.locator(".terminal-screen"), panel.getByText(/Altitude restarts at its next quiet point/)],
  });
  await panel.getByRole("button", { name: "Open a new terminal" }).click();
  await run(page, "echo again-$((4+4))");
  await expect(output).toContainText("again-8");

  await panel.getByRole("button", { name: phone ? "Close" : "Close terminal" }).click();
  await expect(page).toHaveURL(/\/projects\/atlas$/);
  await walk.state("15-closed-back-to-project", { visible: [page.getByRole("button", { name: "Terminal", exact: true })], hidden: [panel] });
  // Closing ended the shell: the terminal opens fresh next time.
  await page.getByRole("button", { name: "Terminal", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Open terminal" })).toBeVisible();
});
