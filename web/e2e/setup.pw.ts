import { expect, type Locator } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "project-setup-service.py" });
test.setTimeout(60_000);
const setupPanel = (page: import("@playwright/test").Page) => page.getByRole("dialog", { name: "Project setup" });
const step = (panel: Locator, label: string) => panel.getByRole("listitem").filter({ has: panel.page().getByRole("heading", { name: label, exact: true }) });

test("fresh registration shows real automatic progress, survives refresh and completes", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  await walk.open(`${service}/projects/atlas`);
  if (info.project.name === "phone") await page.getByRole("button", { name: "atlas", exact: true }).click();
  else await page.getByRole("button", { name: "Add a folder" }).click();
  const firstRun = page.getByRole("region", { name: "First run" });
  const folder = firstRun.getByRole("listitem").filter({ hasText: "new-project" });
  await walk.state("01-discovered-folder", { visible: [folder, folder.getByRole("button", { name: "Add project" })], hidden: [] });
  await folder.getByRole("button", { name: "Add project" }).click();
  const panel = setupPanel(page);
  const coordinator = step(panel, "Coordinator");
  await expect(page).toHaveURL(`${service}/projects/new-project?setup=1`);
  await walk.state("02-automatic-progress", { visible: [panel, coordinator.getByText("In progress", { exact: true }), step(panel, "Git guards").getByText(/Installed and verified/)], hidden: [firstRun] });
  await page.reload();
  await walk.state("03-reconnected-progress", { visible: [panel, coordinator.getByText("In progress", { exact: true })], hidden: [] });
  await request.post(`${service}/fixture/release-intro`);
  await expect(page.getByRole("button", { name: "Setup: Ready" })).toBeVisible();
  await walk.state("04-verified-ready", { visible: [coordinator.getByText("Complete", { exact: true }), step(panel, "Project instructions").getByText("Using AGENTS.md; its contents are unchanged.")], hidden: [coordinator.getByText("In progress", { exact: true })] });
  await panel.getByRole("button", { name: "Open conversation" }).click();
  await walk.state("05-conversation", { visible: [page.getByText("The project conversation is ready."), page.getByRole("textbox", { name: "Message L3 about new-project" })], hidden: [panel] });
  const current = await (await request.get(`${service}/api/setup/new-project`)).json();
  expect(current.status).toBe("ready");
  const evidence = await (await request.get(`${service}/fixture/evidence`)).json();
  expect(evidence.calls.filter((call: { project: string }) => call.project === "new-project")).toHaveLength(1);
});

test("existing projects expose new missing requirements and repair stale guards without losing continuity", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  const baseline = await (await request.get(`${service}/fixture/evidence`)).json();
  await walk.open(`${service}/projects/atlas`);
  const composer = page.getByRole("textbox", { name: "Message L3 about atlas" });
  await composer.fill("Keep this draft while I inspect setup");
  const trigger = page.getByRole("button", { name: "Setup: Ready" });
  await trigger.click();
  const panel = setupPanel(page);
  await walk.state("01-healthy-existing", { visible: [panel, step(panel, "Coordinator").getByText(/Using the existing conversation/)], hidden: [panel.getByRole("button", { name: "Repair", exact: true })] });
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await expect(composer).toHaveValue("Keep this draft while I inspect setup");
  await request.post(`${service}/fixture/prepare/missing`);
  // Polling may already have replaced Ready with the new observed setup status.
  await page.getByRole("button", { name: /^Setup:/ }).click();
  const guards = step(panel, "Git guards");
  await walk.state("02-new-missing-requirement", { visible: [guards.getByText("Git guards are not installed."), guards.getByRole("button", { name: "Repair" })], hidden: [] });
  await guards.getByRole("button", { name: "Repair" }).click();
  await walk.state("03-missing-repaired", { visible: [guards.getByText(/Installed and verified/)], hidden: [guards.getByRole("button", { name: "Repair" })] });
  await request.post(`${service}/fixture/prepare/stale`);
  await panel.getByRole("button", { name: "Check again" }).click();
  await walk.state("04-stale-source-guards", { visible: [guards.getByText("Git guards need an update."), guards.getByRole("button", { name: "Repair" })], hidden: [] });
  await guards.getByRole("button", { name: "Repair" }).click();
  await walk.state("05-stale-guards-updated", { visible: [guards.getByText(/Updated and verified/)], hidden: [guards.getByRole("button", { name: "Repair" })] });
  await panel.getByRole("button", { name: "Open conversation" }).click();
  await expect(composer).toHaveValue("Keep this draft while I inspect setup");
  const after = await (await request.get(`${service}/fixture/evidence`)).json();
  expect(after.hooks.status).toBe("ready");
  expect(after.session).toBe(baseline.session);
  expect(after.task).toEqual(baseline.task);
  expect(after.history.slice(0, baseline.history.length)).toEqual(baseline.history);
  expect(after.history.slice(baseline.history.length).every((row: { role: string; trigger: string }) => row.role === "system" && row.trigger === "fyi")).toBe(true);
  expect(after.calls).toEqual([]);
});

test("custom hooks require explicit integration, retain their files and actually run", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  await request.post(`${service}/fixture/prepare/custom`);
  const original = await (await request.get(`${service}/fixture/evidence`)).json();
  await walk.open(`${service}/projects/atlas?setup=1`);
  const panel = setupPanel(page);
  const guards = step(panel, "Git guards");
  await walk.state("01-custom-hook-conflict", { visible: [guards.getByText("Existing hooks need your integration choice."), guards.getByRole("button", { name: "Review integration" })], hidden: [guards.getByRole("button", { name: "Use both hook sets" })] });
  await guards.getByRole("button", { name: "Review integration" }).click();
  await walk.state("02-integration-choice", { visible: [guards.getByRole("button", { name: "Use both hook sets" }), guards.getByRole("button", { name: "Keep current setup" })], hidden: [] });
  await guards.getByRole("button", { name: "Keep current setup" }).click();
  expect((await (await request.get(`${service}/fixture/evidence`)).json()).custom).toBe(original.custom);
  await walk.state("03-current-hooks-kept", { visible: [guards.getByRole("button", { name: "Review integration" })], hidden: [guards.getByRole("button", { name: "Use both hook sets" })] });
  await guards.getByRole("button", { name: "Review integration" }).click();
  await guards.getByRole("button", { name: "Use both hook sets" }).click();
  await walk.state("04-both-hook-sets-verified", { visible: [guards.getByText("Both hook sets are configured and verified.")], hidden: [guards.getByRole("button", { name: "Use both hook sets" }), guards.getByRole("button", { name: "Review integration" })] });
  expect((await (await request.get(`${service}/fixture/evidence`)).json()).custom).toBe(original.custom);
  const ran = await request.post(`${service}/fixture/run-custom`);
  expect(ran.ok()).toBe(true);
  expect((await ran.json()).ran).toBe("custom hook ran\n");
});

test("failed and interrupted setup retries safely; a denied request preserves the observed failure", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  await request.post(`${service}/fixture/prepare/failure`);
  await walk.open(`${service}/projects/atlas?setup=1`);
  const panel = setupPanel(page);
  const guards = step(panel, "Git guards");
  await guards.getByRole("button", { name: "Repair" }).click();
  await walk.state("01-actual-git-write-failed", { visible: [guards.getByText("Failed", { exact: true }), guards.getByRole("button", { name: "Retry", exact: true }), panel.getByRole("button", { name: "Discuss with L3", exact: true })], hidden: [] });
  const failed = await (await request.get(`${service}/api/setup/atlas`)).json();
  expect(failed.operation.state).toBe("failed");
  await page.route("**/api/project/setup", (route) => route.fulfill({ status: 403, json: { error: "Repair request is denied" } }), { times: 1 });
  await guards.getByRole("button", { name: "Retry", exact: true }).click();
  await walk.state("02-request-denied", { visible: [panel.getByText("Request refused. No action was accepted."), guards.getByText("Failed", { exact: true })], hidden: [] });
  expect((await (await request.get(`${service}/api/setup/atlas`)).json()).operation.id).toBe(failed.operation.id);
  await request.post(`${service}/fixture/prepare/clear-lock`);
  await guards.getByRole("button", { name: "Retry", exact: true }).click();
  await walk.state("03-successful-retry", { visible: [guards.getByText(/Installed and verified/)], hidden: [guards.getByRole("button", { name: "Retry", exact: true }), panel.getByText("Request refused. No action was accepted.")] });
  await request.post(`${service}/fixture/prepare/interrupted`);
  await page.reload();
  await walk.state("04-interrupted-record-reconciled", { visible: [guards.getByText("Recheck needed", { exact: true }), guards.getByRole("button", { name: "Retry", exact: true })], hidden: [] });
  const beforeDiscuss = await (await request.get(`${service}/api/chat/atlas`)).json();
  await panel.getByRole("button", { name: "Discuss with L3", exact: true }).click();
  await walk.state("05-discussion-preserves-conversation", { visible: [page.getByText("Saved project history."), page.getByRole("textbox", { name: "Message L3 about atlas" })], hidden: [panel] });
  expect((await (await request.get(`${service}/api/chat/atlas`)).json()).history).toEqual(beforeDiscuss.history);
  await page.getByRole("button", { name: "Setup: Needs attention" }).click();
  await guards.getByRole("button", { name: "Retry", exact: true }).click();
  await walk.state("06-interrupted-retry-verified", { visible: [guards.getByText(/Installed and verified/)], hidden: [guards.getByRole("button", { name: "Retry", exact: true })] });
});

test("loading, empty discovery, offline saved results and reconnection remain actionable", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/setup/atlas", async (route) => { await held; await route.continue(); }, { times: 1 });
  await walk.open(`${service}/projects/atlas?setup=1`);
  const panel = setupPanel(page);
  await walk.state("01-loading-setup", { visible: [panel.getByLabel("Loading setup")], hidden: [panel.getByRole("button", { name: "Repair" })] });
  release();
  await expect(page.getByRole("button", { name: "Setup: Ready" })).toBeVisible();
  await panel.getByRole("button", { name: "Close setup" }).click();
  const failRead = (route: import("@playwright/test").Route) => route.abort("connectionfailed");
  await page.route("**/api/setup/atlas", failRead);
  await page.getByRole("button", { name: "Setup: Ready" }).click();
  // The production query retries its read before declaring the saved observation stale.
  await expect(panel.getByText("Showing saved results. Current setup could not be checked.")).toBeVisible({ timeout: 15_000 });
  await walk.state("02-offline-saved-results", { visible: [panel.getByText("Showing saved results. Current setup could not be checked."), panel.getByRole("button", { name: "Retry connection" })], hidden: [] });
  await page.unroute("**/api/setup/atlas", failRead);
  await panel.getByRole("button", { name: "Retry connection" }).click();
  await walk.state("03-reconnected-current-results", { visible: [page.getByRole("button", { name: "Setup: Ready" })], hidden: [panel.getByRole("button", { name: "Retry connection" })] });
  await panel.getByRole("button", { name: "Close setup" }).click();
  await request.post(`${service}/fixture/prepare/empty-discovery`);
  await page.reload();
  if (info.project.name === "phone") {
    await page.getByRole("button", { name: "atlas", exact: true }).click();
  } else await page.getByRole("button", { name: "Add a folder" }).click();
  await walk.state("04-empty-folder-discovery", { visible: [page.getByRole("region", { name: "First run" }), page.getByRole("region", { name: "Choose a folder" })], hidden: [page.getByRole("region", { name: "First run" }).getByRole("button", { name: "Add project" })] });
});

test("a failed first conversation exposes Retry and completes the existing setup", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  await request.post(`${service}/fixture/fail-intro`);
  const overview = await (await request.get(`${service}/api/overview`)).json();
  const folder = overview.projects.find((row: { name: string }) => row.name === "new-project");
  expect((await request.post(`${service}/api/project/add`, { data: { name: folder.name, path: folder.path } })).ok()).toBe(true);
  expect((await request.post(`${service}/fixture/wait-intro`)).ok()).toBe(true);
  await walk.open(`${service}/projects/new-project?setup=1`);
  const panel = setupPanel(page);
  const coordinator = step(panel, "Coordinator");
  await walk.state("01-first-conversation-failed", { visible: [coordinator.getByText("Failed", { exact: true }), coordinator.getByRole("button", { name: "Retry", exact: true })], hidden: [] });
  await request.post(`${service}/fixture/retry-intro`);
  await coordinator.getByRole("button", { name: "Retry", exact: true }).click();
  await walk.state("02-first-conversation-recovered", { visible: [coordinator.getByText("Complete", { exact: true }), page.getByRole("button", { name: "Setup: Ready" })], hidden: [coordinator.getByRole("button", { name: "Retry", exact: true })] });
  const evidence = await (await request.get(`${service}/fixture/evidence`)).json();
  expect(evidence.calls.filter((call: { project: string }) => call.project === "new-project")).toHaveLength(2);
});

test("conversation-only folders report not-applicable checks and recover an initial read error", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  const notes = await (await request.post(`${service}/fixture/notes`)).json();
  expect((await request.post(`${service}/api/project/add`, { data: { name: "notes", path: notes.path } })).ok()).toBe(true);
  const failRead = (route: import("@playwright/test").Route) => route.fulfill({ status: 503, json: { error: "Setup observation is temporarily unavailable" } });
  await page.route("**/api/setup/notes", failRead);
  await walk.open(`${service}/projects/notes?setup=1`);
  const panel = setupPanel(page);
  await expect(panel.getByText("Could not read project setup.")).toBeVisible({ timeout: 15_000 });
  await walk.state("01-initial-read-error", { visible: [panel.getByText("Could not read project setup."), panel.getByRole("button", { name: "Retry connection" })], hidden: [panel.getByRole("heading", { name: "Git guards" })] });
  await page.unroute("**/api/setup/notes", failRead);
  await panel.getByRole("button", { name: "Retry connection" }).click();
  await walk.state("02-conversation-only-ready", { visible: [page.getByRole("button", { name: "Setup: Conversation ready" }), step(panel, "Git repository").getByText("Not applicable", { exact: true }), step(panel, "Git guards").getByText("Not applicable", { exact: true }), step(panel, "Project instructions").getByText(/No project instructions/)], hidden: [panel.getByRole("button", { name: "Retry connection" })] });
  const view = await (await request.get(`${service}/api/setup/notes`)).json();
  expect(view.status).toBe("conversation_ready");
  await step(panel, "Project instructions").getByRole("button", { name: "Discuss with L3" }).click();
  await walk.state("03-discuss-optional-instructions", { visible: [page.getByRole("textbox", { name: "Message L3 about notes" })], hidden: [panel] });
});
