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
    visible: [output.getByText("prepare-index-migration", { exact: false }).last(), close, panel.getByText("This task's owner can read this terminal's output, and what it reads reaches its AI provider.")],
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
  else await page.keyboard.press("ControlOrMeta+V");
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
    // Ctrl+C (Cmd+C on a Mac) with text selected copies it; the line being typed is not interrupted.
    await page.evaluate(() => navigator.clipboard.writeText(""));
    await page.keyboard.type("echo still-");
    const word = await output.getByText("pasted-14").last().boundingBox();
    await page.mouse.dblclick(word!.x + 12, word!.y + word!.height / 2);
    await page.keyboard.press("ControlOrMeta+C");  // copy is Cmd+C on a Mac, where Ctrl+C always interrupts
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

/**
 * A finger on the terminal screen: Chromium's touch input (real dispatch, hit testing and native gesture
 * handling) or, in WebKit, which has neither touch drag input nor constructible touch events here, events
 * carrying the finger's touches dispatched on the screen itself.
 */
async function finger(page: Page, x: number, y: number) {
  const chromium = page.context().browser()?.browserType().name() === "chromium";
  const input = chromium ? await page.context().newCDPSession(page) : null;
  const touch = (type: "touchStart" | "touchMove" | "touchEnd", point?: { x: number; y: number }) => input
    ? input.send("Input.dispatchTouchEvent", { type, touchPoints: point ? [point] : [] })
    : page.evaluate(({ type, point }) => {
      const target = document.querySelector(".terminal-screen .xterm-screen")!;
      const event = new Event(type.toLowerCase(), { bubbles: true, cancelable: true });
      Object.defineProperty(event, "touches", { value: point ? [{ identifier: 1, target, clientX: point.x, clientY: point.y }] : [] });
      target.dispatchEvent(event);
    }, { type, point });
  await touch("touchStart", { x, y });
  let at = { x, y };
  return {
    /** Moves in steps; a pause after the last step lets the finger rest, so lifting it does not fling. */
    async move(dy: number, pause = 0) {
      const from = at;
      for (let step = 1; step <= 8; step++) {
        at = { x, y: from.y + dy * step / 8 };
        await touch("touchMove", at);
      }
      if (pause) await page.waitForTimeout(pause);
    },
    async lift() {
      try { await touch("touchEnd"); } finally { await input?.detach(); }
    },
  };
}

test("the terminal scrolls back through its output by touch or wheel and follows new output at the bottom", async ({ page, request }, info) => {
  test.setTimeout(90_000);
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const output = page.locator(".terminal-screen .xterm-rows");
  const screen = page.locator(".terminal-screen");
  const row = (n: number) => output.getByText(`row-${String(n).padStart(3, "0")}`, { exact: true });
  /** The first numbered row on screen. */
  const top = async () => Number((await output.innerText()).match(/row-(\d{3})/)?.[1] ?? 0);
  const inputs: string[] = [];
  page.on("request", (sent) => { if (sent.url().endsWith("/input")) inputs.push(sent.postData() ?? ""); });
  expect((await request.post("/api/terminal-access", { data: { enabled: true } })).ok()).toBe(true);

  await walk.open("/projects/atlas");
  await page.getByRole("button", { name: "Terminal", exact: true }).click();
  await expect(output).toContainText("Runs as you in ");
  // A real shell prints several screens, then waits twice for a line sent from elsewhere so output arrives
  // while this page is scrolled back and again at the bottom, without this page's typing moving the view.
  await run(page, "seq -f 'row-%03g' 1 150; read -r; seq -f 'late-%03g' 1 40; read -r; echo followed-again");
  await expect(row(150)).toBeVisible();
  await walk.state("16-scroll-at-bottom", { visible: [row(150)], hidden: [row(1)] });
  const box = (await screen.boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 3;
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  inputs.length = 0;

  if (phone) {
    // A resting drag moves the text with the finger and stops when it lifts.
    const drag = await finger(page, x, y);
    await drag.move(340, 200);
    await drag.lift();
    await expect(row(150)).toBeHidden();
    const rested = await top();
    expect(rested).toBeGreaterThan(80);
    await page.waitForTimeout(300);
    expect(await top()).toBe(rested);
    // A quick swipe flings on after the finger lifts, further than the finger moved.
    const swipe = await finger(page, x, y);
    await swipe.move(120);
    await swipe.lift();
    const lifted = await top();
    await expect.poll(top, { timeout: 5_000 }).toBeLessThan(lifted);
    // The fling has come to rest once the view holds still.
    await expect.poll(async () => {
      const before = await top();
      await page.waitForTimeout(400);
      return before === await top();
    }, { timeout: 10_000 }).toBe(true);
  } else {
    await page.mouse.move(x, y);
    await page.mouse.wheel(0, -1200);
    await expect(row(150)).toBeHidden();
  }
  // A fast runner's fling can reach the first line.
  const reading = await top();
  expect(reading).toBeGreaterThan(0);
  // Scrolling reaches only the display: nothing is typed and focus, and so the soft keyboard, stays away.
  expect(inputs).toEqual([]);
  expect(await page.evaluate(() => document.activeElement?.classList.contains("xterm-helper-textarea") ?? false)).toBe(false);
  await walk.state("17-scrolled-back", { visible: [row(reading)], hidden: [row(150)] });

  // Output arriving while the operator reads earlier lines leaves them in place.
  const terminal = await (await request.get("/api/terminal/atlas")).json();
  expect((await request.post("/api/terminal/atlas/input", { data: { id: terminal.id, data: "\r" } })).ok()).toBe(true);
  await page.waitForTimeout(1_000);
  expect(await top()).toBe(reading);
  await walk.state("18-new-output-keeps-view", { visible: [row(reading)], hidden: [output.getByText("late-040")] });

  // Back at the bottom the screen follows new output again: moving on until one more move changes nothing.
  const down = async () => {
    if (phone) {
      const drag = await finger(page, x, box.y + box.height - 40);
      await drag.move(-(box.height - 80), 150);
      await drag.lift();
    } else await page.mouse.wheel(0, 1_200);
    // The screen draws on the next frame.
    await page.waitForTimeout(100);
  };
  await expect(async () => {
    await down();
    const shown = await output.innerText();
    await down();
    expect(await output.innerText()).toBe(shown);
  }).toPass({ timeout: 30_000 });
  await expect(output.getByText("late-040")).toBeVisible();
  expect((await request.post("/api/terminal/atlas/input", { data: { id: terminal.id, data: "\r" } })).ok()).toBe(true);
  await expect(output.getByText("followed-again")).toBeVisible();
  await walk.state("19-back-at-bottom-follows", { visible: [output.getByText("followed-again")], hidden: [row(reading)] });

  if (phone) {
    // A key row key returns to the prompt, as typing does.
    const drag = await finger(page, x, y);
    await drag.move(340, 200);
    await drag.lift();
    await expect(output.getByText("followed-again")).toBeHidden();
    await page.getByRole("toolbar", { name: "Terminal keys" }).getByRole("button", { name: "Left" }).click();
    await expect(output.getByText("followed-again")).toBeVisible();
  }
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/atlas$/);
});
