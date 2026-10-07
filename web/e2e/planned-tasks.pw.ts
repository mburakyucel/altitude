import { expect, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "planned-tasks-service.py" });

const slug = "check-index-compatibility";
const dependency = "migrate-the-index";
const dependent = "measure-migrated-index";
const reason = "the index migration and browser checks to land";
const workPath = "/projects/atlas?tab=work";
const taskPath = `/projects/atlas/tasks/${slug}`;
const readTask = async (request: APIRequestContext, name = slug) => {
  const response = await request.get(`/api/task/atlas/${name}`);
  expect(response.ok()).toBe(true);
  return response.json();
};
const control = async (request: APIRequestContext, action: string) => {
  expect((await request.post("/fixture/control", { data: { action } })).ok()).toBe(true);
};

test("planned work accepts a brief update, releases through capacity, and auto-releases its dependency", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const work = page.getByRole("region", { name: "Work", exact: true });
  const plannedRow = work.getByRole("link", { name: `Check index compatibility · Planned · Waiting for ${reason}.`, exact: true });
  const dependentRow = work.getByRole("link", { name: `Measure migrated index · Planned · Waiting for “Migrate the index” to finish.`, exact: true });
  await walk.open(workPath);
  await walk.state("01-planned-beside-running", { visible: [plannedRow, dependentRow, work.getByRole("link", { name: /^Migrate the index · L2 working/ })], hidden: [] });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const initial = await readTask(request);
  expect(initial).toMatchObject({ state: "queued", attempt: 0, worktree: null, planned_wait: { reason, after: null } });
  const capacity = (await (await request.get("/api/overview")).json()).wip;
  expect(capacity).toMatchObject({ per_project: { atlas: 1 }, machine: 1, limit_machine: 1 });
  expect(capacity).not.toHaveProperty("limit_project");
  expect(capacity).not.toHaveProperty("limits_per_project");
  await plannedRow.click();
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const notice = page.locator(".task-explanation").getByText(`Waiting for ${reason}.`, { exact: true });
  await walk.state("02-planned-empty-conversation", { visible: [field, notice, conversation.getByText("No messages yet.", { exact: true })], hidden: [conversation.getByRole("button", { name: /^Stop/ })] });
  const update = "Keep pagination tokens compatible after the index migration.";
  await field.fill(update);
  const receipt = page.waitForResponse((response) => response.url().endsWith("/api/l2/message"));
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  expect((await receipt).ok()).toBe(true);
  await expect(field).toHaveValue("");
  await walk.state("03-message-saved-still-planned", { visible: [notice, conversation.getByText(update, { exact: true }), conversation.getByText("Queued · waiting for the L2 to start", { exact: true })], hidden: [conversation.locator(".msg-row[data-pending]")] });
  expect(await readTask(request)).toMatchObject({ state: "queued", attempt: 0, worktree: null, planned_wait: { reason } });
  let evidence = await (await request.get("/fixture/status")).json();
  expect(evidence.calls).toHaveLength(1);
  expect(evidence.pending.map((row: { text: string }) => row.text)).toEqual([update]);
  expect(evidence.request).toBe("Keep the original pagination contract.\n");

  await control(request, "release");
  await walk.open(workPath);
  const queuedRow = work.getByRole("link", { name: /^Check index compatibility · Queued · waits for a free task slot/ });
  await walk.state("04-released-queued-at-capacity", { visible: [queuedRow, dependentRow], hidden: [plannedRow] });
  await queuedRow.click();
  await page.getByRole("button", { name: /Task details$/ }).click();
  await walk.state("04b-capacity-evidence-in-details", {
    visible: [page.getByRole("dialog", { name: "Task details", exact: true }).getByText("WIP limit: 1 running on this machine", { exact: true })], hidden: [],
  });
  await walk.open(workPath);
  const released = await readTask(request);
  expect(released.state).toBe("queued");
  expect(released.planned_wait).toBeFalsy();
  expect(released.events.find((row: { kind: string }) => row.kind === "released")).toMatchObject({ by: "l3", reason: "The operator verified both prerequisites." });

  await control(request, "complete-dependency");
  await walk.open(workPath);
  const runningRow = work.getByRole("link", { name: /^Check index compatibility · L2 replying to you/ });
  const dependentQueued = work.getByRole("link", { name: /^Measure migrated index · Queued · waits for a free task slot/ });
  await walk.state("05-running-and-dependency-auto-released", { visible: [runningRow, dependentQueued], hidden: [queuedRow, dependentRow] });
  expect((await readTask(request, dependency)).state).toBe("done");
  expect((await readTask(request)).state).toBe("running");
  const automaticallyReleased = await readTask(request, dependent);
  expect(automaticallyReleased.planned_wait).toBeFalsy();
  expect(automaticallyReleased.events.find((row: { kind: string }) => row.kind === "released")).toMatchObject({ by: "altd", reason: `${dependency} archived done` });
  evidence = await (await request.get("/fixture/status")).json();
  expect(evidence.calls).toHaveLength(2);
  expect(evidence.calls[1].prompt).toContain(update);
  expect(evidence.pending).toEqual([]);
  expect(evidence.request).toBe("Keep the original pagination contract.\n");
  await control(request, "complete-planned");
  await walk.open(workPath);
  await walk.state("06-named-dependent-running", { visible: [work.getByRole("link", { name: /^Measure migrated index · L2 working/ })], hidden: [dependentQueued] });
});

test("planned-message denial retains the draft and restores sending after access returns", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await control(request, "deny");
  await walk.open(taskPath);
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const text = "Preserve the compatibility scope.";
  await field.fill(text);
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  const denial = conversation.getByRole("alert").filter({ hasText: "You cannot send messages or answers here." });
  await walk.state("01-denied-draft-retained", { visible: [field, denial], hidden: [conversation.locator(".msg-row[data-pending]")] });
  await expect(field).toHaveValue(text);
  await expect(field).toBeDisabled();
  expect((await readTask(request)).messages).toEqual([]);
  await control(request, "allow");
  await denial.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(field).toBeEnabled();
  await walk.state("02-access-restored-with-draft", { visible: [field], hidden: [denial] });
  await expect(field).toHaveValue(text);
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  await expect(field).toHaveValue("");
  await walk.state("03-saved-after-access-restored", { visible: [conversation.getByText(text, { exact: true }), page.locator(".task-explanation").getByText(`Waiting for ${reason}.`, { exact: true })], hidden: [denial] });
  expect((await readTask(request)).planned_wait).toMatchObject({ reason });
});

test("Work exposes loading, failed reads, recovery and empty state around planned tasks", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const work = page.getByRole("region", { name: "Work", exact: true });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/project/atlas", async (route) => { await gate; await route.continue(); }, { times: 1 });
  await walk.open(workPath);
  await walk.state("01-work-loading", { visible: [work.getByLabel("Loading", { exact: true })], hidden: [work.getByRole("region", { name: "Current", exact: true })] });
  release();
  const planned = work.getByRole("link", { name: /^Check index compatibility · Planned/ });
  await walk.state("02-work-loaded", { visible: [planned], hidden: [work.getByLabel("Loading", { exact: true })] });
  await page.route("**/api/project/atlas", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "Fixture read unavailable" }) }));
  await page.reload();
  const error = work.getByRole("alert").filter({ hasText: "Could not read the project's work." });
  await expect(error).toBeVisible({ timeout: 15_000 }); // The normal query exhausts its read retries.
  await walk.state("03-work-read-failed", { visible: [error], hidden: [planned] });
  await page.unroute("**/api/project/atlas");
  await error.getByRole("button", { name: "Retry", exact: true }).click();
  await walk.state("04-work-read-recovered", { visible: [planned], hidden: [error] });
  await control(request, "empty");
  await page.reload();
  await walk.state("05-work-empty", { visible: [work.getByText("No current tasks. Ask L3 to start something.", { exact: true })], hidden: [planned, work.getByRole("region", { name: "Current", exact: true })] });
});
