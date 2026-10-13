import { expect } from "@playwright/test";
import type { APIRequestContext, Locator } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "task-transfer-service.py" });

const coordinator = async (request: APIRequestContext) => (await (await request.get("/api/defaults/atlas")).json()).l3_choice;
const newTasks = async (request: APIRequestContext) => (await (await request.get("/api/overview")).json()).new_tasks.value;

const column = (menu: Locator, name: "Model" | "Effort") => menu.getByRole("group", { name, exact: true });

test("the chat's model and effort button picks only the coordinator's choice, each pick saved at once", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const initial = await coordinator(request);
  const tasksBefore = await newTasks(request);
  await walk.open("/projects/atlas");
  const button = page.getByRole("button", { name: /^Coordinator model and effort:/ });
  await walk.state("01-chat-button", { visible: [button], hidden: [page.getByRole("menu")] });
  await button.click();
  const panel = page.getByRole("menu", { name: "Coordinator model and effort for atlas", exact: true });
  const models = column(panel, "Model");
  const efforts = column(panel, "Effort");
  // Each model appears once, beside the effort levels; opening saves nothing.
  await walk.state("02-panel", { visible: [models.getByRole("menuitemradio", { name: /^Auto/, checked: true }), efforts.getByRole("menuitemradio", { name: /^Default/, checked: true })],
    hidden: [models.getByRole("menuitemradio", { name: "Default", exact: true })] });
  expect(await coordinator(request)).toEqual(initial);
  await models.getByRole("menuitemradio", { name: "Opus", exact: true }).click();
  await expect.poll(() => coordinator(request)).toMatchObject({ engine: "claude", model: "opus" });
  // The panel stays open for the other half of the choice; effort lists only what the chosen model's engine runs.
  await expect(efforts.getByRole("menuitemradio", { name: "Max", exact: true })).toBeVisible();
  await expect(efforts.getByRole("menuitemradio", { name: "Ultra", exact: true })).toBeHidden();
  await efforts.getByRole("menuitemradio", { name: "High", exact: true }).click();
  await expect.poll(() => coordinator(request)).toMatchObject({ model: "opus", effort: "high" });
  await expect(efforts.getByRole("menuitemradio", { name: "High", exact: true })).toHaveAttribute("aria-checked", "true");
  await expect(button).toHaveAccessibleName("Coordinator model and effort: Opus · High");
  await walk.state("03-saved", { visible: [panel, models.getByRole("menuitemradio", { name: "Opus", checked: true })], hidden: [] });
  // A model on another engine keeps the effort when that engine runs it.
  await models.getByRole("menuitemradio", { name: "6.1 Sol", exact: true }).click();
  await expect.poll(() => coordinator(request)).toMatchObject({ engine: "codex", model: "gpt-6.1-sol", effort: "high" });
  // Every engine's list starts with Default, which says what it runs.
  await expect(efforts.getByRole("menuitemradio", { name: "Ultra", exact: true })).toBeVisible();
  await walk.state("04-codex-levels", { visible: [efforts.getByRole("menuitemradio", { name: /^Default/ })], hidden: [] });
  await page.keyboard.press("Escape");
  await expect(panel).toBeHidden();
  await expect(button).toBeFocused();
  await walk.state("05-closed-after-picks", { visible: [button], hidden: [panel] });
  expect(await newTasks(request)).toEqual(tasksBefore);
  // A refused save keeps the panel open with the reason and Retry; nothing else moves.
  await page.route("**/api/defaults", (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 500, json: { error: "The settings file is read-only." } }) : route.continue(), { times: 1 });
  await button.click();
  await models.getByRole("menuitemradio", { name: "Sonnet", exact: true }).click();
  // The note names the failed pick and appears at the panel's far edge, so the row under the pointer stays put.
  await expect(panel.getByRole("alert")).toContainText("Could not save Sonnet · High.");
  await walk.state("06-save-failed", { visible: [panel.getByRole("alert").getByRole("button", { name: "Retry", exact: true })], hidden: [] });
  expect(await coordinator(request)).toMatchObject({ model: "gpt-6.1-sol" });
  await page.keyboard.press("Escape");
  await expect(panel).toBeHidden();
  // While a save is in flight the pressed row carries a progress mark in place of the check.
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/defaults", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await held;
    await route.continue();
  }, { times: 1 });
  await button.click();
  await efforts.getByRole("menuitemradio", { name: "Max", exact: true }).click();
  await walk.state("07-saving", { visible: [efforts.getByRole("menuitemradio", { name: "Max", exact: true }).locator(".spinner")], hidden: [] });
  release();
  await expect.poll(() => coordinator(request)).toMatchObject({ effort: "max" });
  // A choice changed in another window is not overwritten: Reload shows it.
  await page.route("**/api/defaults", (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 409, json: { error: "The setting changed." } }) : route.continue(), { times: 1 });
  await efforts.getByRole("menuitemradio", { name: "High", exact: true }).click();
  await walk.state("08-changed-elsewhere", { visible: [panel.getByRole("alert").getByRole("button", { name: "Reload", exact: true })], hidden: [] });
  await page.keyboard.press("Escape");
  await expect(panel).toBeHidden();
  // A failed read says so on the closed button and offers Retry in place of the choices.
  await page.route("**/api/defaults/atlas", (route) => route.fulfill({ status: 500, json: { error: "unavailable" } }));
  await page.reload();
  const failed = page.getByRole("button", { name: "Coordinator model and effort: could not load", exact: true });
  await expect(failed).toBeVisible();
  await failed.click();
  await walk.state("09-load-failed", { visible: [page.getByRole("menu").getByRole("alert")], hidden: [] });
});

test("the New tasks button picks what every project's new tasks start on", async ({ page, request }, info) => {
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const before = await coordinator(request);
  await walk.open(phone ? "/monitor" : "/projects/atlas");
  const button = page.getByRole("button", { name: /^Model and effort for new tasks:/ });
  await walk.state("01-new-tasks-button", { visible: [button], hidden: [page.getByRole("menu")] });
  await button.click();
  const panel = page.getByRole("menu", { name: "Model and effort for new tasks", exact: true });
  await walk.state("02-new-tasks-panel", { visible: [column(panel, "Model"), column(panel, "Effort").getByText("Project setting or built-in")], hidden: [] });
  expect(await coordinator(request)).toEqual(before);
  await column(panel, "Model").getByRole("menuitemradio", { name: "6.1 Sol", exact: true }).click();
  await expect.poll(() => newTasks(request)).toMatchObject({ model: "gpt-6.1-sol" });
  await page.keyboard.press("Escape");
  await expect(button).toContainText("6.1 Sol");
  await walk.state("03-new-tasks-saved", { visible: [button], hidden: [panel] });
  expect(await coordinator(request)).toEqual(before);
  // On a phone the same button also leads a project's Work; on a desktop the rail carries it beside Work.
  await walk.open("/projects/atlas");
  if (phone) await page.getByRole("navigation", { name: "Primary", exact: true }).getByRole("link", { name: "Work", exact: true }).click();
  await walk.state("04-beside-work", { visible: [page.getByRole("region", { name: "Work", exact: true }), button], hidden: [] });
});

test("a task's button: browsing requests nothing; model and effort make one pending choice confirmed once", async ({ page, request }, info) => {
  const slug = "transfer-running-work";
  const read = async () => (await (await request.get(`/api/task/atlas/${slug}`)).json());
  const before = await read();
  const other = before.transfer.targets.find((item: { engine: string; available: boolean }) => item.available && item.engine !== before.transfer.current_engine);
  const walk = walkthrough(page, info);
  await walk.open(`/projects/atlas/tasks/${slug}`);
  const button = page.getByRole("button", { name: /^Task model and effort:/ });
  const panel = page.getByRole("menu", { name: "This task’s model and effort", exact: true });
  const confirm = panel.getByRole("group", { name: "Confirm the switch", exact: true });
  await button.click();
  await walk.state("01-task-panel", { visible: [column(panel, "Model").getByText("Current", { exact: true }), column(panel, "Effort")], hidden: [confirm] });
  expect((await read()).daemon_request).toBeFalsy();
  await column(panel, "Effort").getByRole("menuitemradio", { name: "Medium", exact: true }).click();
  // Only the effort changes, so the title names only the effort; the pick is marked apart from the running level.
  await expect(confirm.getByText("Switch to Medium effort?", { exact: true })).toBeVisible();
  await expect(column(panel, "Effort").getByRole("menuitemradio", { name: "Medium", exact: true })).toHaveAttribute("data-pending");
  await walk.state("02-effort-only-confirm", { visible: [confirm, confirm.getByText(/catching up uses extra tokens/)], hidden: [] });
  expect((await read()).daemon_request).toBeFalsy();
  // Cancel drops the pending choice and closes the panel.
  await confirm.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(panel).toBeHidden();
  await expect(button).toBeFocused();
  await expect(button).not.toContainText("Medium");
  // Model and effort change together in one confirmation.
  await button.click();
  await column(panel, "Model").getByRole("group", { name: other.label, exact: true }).getByRole("menuitemradio").first().click();
  await expect(confirm.getByText(new RegExp(`^Switch to .+ on ${other.label} · .+\\?$`))).toBeVisible();
  await column(panel, "Effort").getByRole("menuitemradio", { name: "Max", exact: true }).click();
  await expect(confirm.getByText(new RegExp(`^Switch to .+ on ${other.label} · Max\\?$`))).toBeVisible();
  await walk.state("03-engine-switch-confirm", { visible: [confirm], hidden: [] });
  expect((await read()).daemon_request).toBeFalsy();
  await confirm.getByRole("button", { name: "Switch", exact: true }).click();
  await expect(panel).toBeHidden();
  const card = page.getByRole("region", { name: "Task conversation", exact: true }).getByRole("region", { name: "Model switch", exact: true });
  await expect(card.getByText("Started by you", { exact: true })).toBeVisible();
  await expect(card.getByText(/^Switching to .+ · Max$/)).toBeVisible();
  await walk.state("04-switching", { visible: [card, page.getByRole("button", { name: /^Task model: switching to / })], hidden: [] });
  expect((await read()).daemon_request.actor).toBeTruthy();
  await card.getByRole("button", { name: "Cancel switch", exact: true }).click();
  await expect.poll(async () => (await read()).transfer.request.status).toBe("cancelled");
  // A lost response is checked by reading, never sent again.
  await page.route("**/api/task/action", (route) => route.abort(), { times: 1 });
  await button.click();
  await column(panel, "Model").getByRole("group", { name: other.label, exact: true }).getByRole("menuitemradio").first().click();
  await confirm.getByRole("button", { name: "Switch", exact: true }).click();
  await expect(card.getByText(/^Could not confirm the switch\./)).toBeVisible();
  // The button offers no second switch; it leads to the card that settles this one.
  const unconfirmed = page.getByRole("button", { name: /Switch unconfirmed; show the switch$/ });
  await walk.state("05-unconfirmed", { visible: [card.getByRole("button", { name: "Check status", exact: true }), unconfirmed], hidden: [panel] });
  await unconfirmed.click();
  await expect(card).toBeFocused();
  await expect(panel).toBeHidden();
});

test("a stopped task offers the panel from its usage-limit card; ruled-out models say why; a switch the coordinator starts says so", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const stopped = (await (await request.get("/api/task/atlas/transfer-quota-work")).json()).transfer;
  const other = stopped.targets.find((item: { engine: string; available: boolean }) => item.available && item.engine !== stopped.current_engine);
  await walk.open("/projects/atlas/tasks/transfer-quota-work");
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const limit = conversation.getByRole("region", { name: "Engine usage limit", exact: true });
  const panel = page.getByRole("menu", { name: "This task’s model and effort", exact: true });
  const confirm = panel.getByRole("group", { name: "Confirm the switch", exact: true });
  await walk.state("06-usage-limit-card", { visible: [limit, limit.getByRole("button", { name: "Change model…", exact: true })], hidden: [panel] });
  await limit.getByRole("button", { name: "Change model…", exact: true }).click();
  await expect(panel).toBeVisible();
  await expect(confirm).toBeHidden();
  await column(panel, "Model").getByRole("group", { name: other.label, exact: true }).getByRole("menuitemradio").first().click();
  // Nothing is running, so there is no handoff note to wait for.
  await expect(confirm.getByText(/^A fresh session continues from the task’s saved work\./)).toBeVisible();
  await walk.state("07-stopped-task-confirm", { visible: [confirm], hidden: [confirm.getByText(/handoff note/)] });
  await confirm.getByRole("button", { name: "Cancel", exact: true }).click();
  // A model the task's pin rules out is muted; pressing it says why and offers nothing to confirm.
  await walk.open("/projects/atlas/tasks/transfer-pinned-work");
  await page.getByRole("button", { name: /^Task model and effort:/ }).click();
  // A muted row stays pressable to explain itself; Playwright would wait on its aria-disabled.
  await panel.locator('[role="menuitemradio"][aria-disabled="true"]').first().click({ force: true });
  await walk.state("08-unavailable-reason", { visible: [panel.getByRole("status")], hidden: [confirm] });
  await page.keyboard.press("Escape");
  const slug = "transfer-running-work";
  await walk.open(`/projects/atlas/tasks/${slug}`);
  expect((await request.post("/fixture/transfer", { data: { slug, l3_switch: true } })).ok()).toBe(true);
  const card = conversation.getByRole("region", { name: "Model switch", exact: true });
  await expect(card.getByText(/^Started by the coordinator · /)).toBeVisible();
  await walk.state("09-coordinator-switch", { visible: [card, page.getByRole("button", { name: /^Task model: switching to / })], hidden: [] });
});
