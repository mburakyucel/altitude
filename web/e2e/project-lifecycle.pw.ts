import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ scenario: "lifecycle" });

test("removal detaches L3: confirm, cancel, denied, error, pending, navigation", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  const more = page.getByRole("button", { name: "More actions" });
  const item = page.getByRole("menuitem", { name: "Remove project", exact: true });
  await walk.open(`${service}/projects/busy-project`);
  await more.click();
  await item.click();
  const denied = page.getByRole("group", { name: "Remove busy-project from Altitude?" });
  await denied.getByRole("button", { name: "Remove", exact: true }).click();
  await walk.state("01-denied", { visible: [denied, page.getByRole("alert").filter({ hasText: /Finish or reject.*existing-work/ })], hidden: [] });
  expect((await (await request.get(`${service}/api/project/busy-project`)).json()).tasks[0].state).toBe("queued");
  await walk.open(`${service}/projects/sample-project`);
  await walk.state("02-closed", { visible: [more], hidden: [item] });
  await walk.state("03-discoverable", { action: () => more.click(), visible: [item], hidden: [] });
  await item.click();
  const confirm = page.getByRole("group", { name: "Remove sample-project from Altitude?" });
  await walk.state("04-confirmation", { visible: [confirm, confirm.getByText(/detaches L3/)], hidden: [item] });
  await walk.state("05-cancel", { action: () => confirm.getByRole("button", { name: "Cancel" }).click(), visible: [item], hidden: [confirm] });
  await item.click();
  await walk.state("06-dismiss", { action: () => page.keyboard.press("Escape"), visible: [more], hidden: [confirm, item] });
  await more.click();
  await item.click();
  await page.route("**/api/project/remove", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "Could not remove the project. Try again." }) }), { times: 1 });
  await confirm.getByRole("button", { name: "Remove", exact: true }).click();
  await walk.state("07-error", { visible: [confirm, page.getByRole("alert").filter({ hasText: "Could not remove" })], hidden: [] });
  let release!: () => void;
  const wait = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/project/remove", async (route) => { await wait; await route.continue(); }, { times: 1 });
  await confirm.getByRole("button", { name: "Remove", exact: true }).click();
  await walk.state("08-pending", { visible: [page.getByRole("button", { name: "Removing…" })], hidden: [page.getByRole("alert")] });
  await expect(confirm.getByRole("button", { name: "Cancel" })).toBeDisabled();
  await expect(more).toBeDisabled();
  release();
  await expect(page).toHaveURL(`${service}/`);
  await walk.state("09-removed-navigation", { visible: [page.getByRole("heading", { name: "Needs you", exact: true })], hidden: [confirm] });
  expect(await page.evaluate(() => localStorage.getItem("altitude.project"))).toBe("busy-project");
  const overview = await (await request.get(`${service}/api/overview`)).json();
  expect(overview.projects.find((project: { name: string }) => project.name === "sample-project").managed).toBe(false);
  expect((await request.post(`${service}/api/chat`, { data: { project: "sample-project", text: "Must be refused" } })).status()).toBe(409);
  // Known HTTP semantics gap: detached project reads return 500; the UI below still explains detach.
  // Assert it explicitly so the fixture's narrow expected-error allowance cannot conceal another failure.
  for (const api of ["/api/project/sample-project", "/api/chat/sample-project?limit=60"]) {
    const removed = await request.get(`${service}${api}`);
    expect(removed.status()).toBe(500);
    expect((await removed.json()).error).toContain("unknown project 'sample-project'; register it first");
  }
  const savedTask = await request.get(`${service}/api/task/sample-project/existing-work`);
  expect(savedTask.status()).toBe(200);
  expect((await savedTask.json()).activity).toMatchObject({ state: "unavailable", commentary: null });
  for (const suffix of ["", "/tasks/existing-work", "/tasks/existing-work/report"]) {
    await walk.open(`${service}/projects/sample-project${suffix}`);
    await walk.state(`10-old-route-${suffix.replaceAll("/", "-") || "project"}`, { visible: [page.getByRole("heading", { name: "Project not managed", exact: true })], hidden: [more, page.getByRole("textbox", { name: "Message L3 about sample-project" })] });
  }
});

test.describe("last project", () => {
  test.use({ single: true });
  test("First run attaches L3 again and restores history, queue and session", async ({ page, request, service }, info) => {
    const walk = walkthrough(page, info);
    await walk.open(`${service}/projects/sample-project`);
    await page.getByRole("button", { name: "More actions" }).click();
    await page.getByRole("menuitem", { name: "Remove project", exact: true }).click();
    await page.getByRole("group", { name: "Remove sample-project from Altitude?" }).getByRole("button", { name: "Remove", exact: true }).click();
    const firstRun = page.getByRole("region", { name: "First run", exact: true });
    // With nothing managed, First run starts again at its first step; each step is skippable.
    await expect(firstRun.getByRole("heading", { name: "Welcome to Altitude" })).toBeVisible();
    await firstRun.getByRole("button", { name: "Skip" }).click();
    await firstRun.getByRole("button", { name: /^Continue/ }).click();
    await firstRun.getByRole("button", { name: "Skip" }).click();
    const row = firstRun.getByRole("listitem").filter({ hasText: "sample-project" });
    await walk.state("01-last-project-removed", { visible: [firstRun, row.getByRole("button", { name: "Add project", exact: true })], hidden: [page.getByRole("button", { name: "More actions" })] });
    expect(await page.evaluate(() => localStorage.getItem("altitude.project"))).toBeNull();
    const overview = await (await request.get(`${service}/api/overview`)).json();
    expect(overview.projects[0].managed).toBe(false);
    await page.route("**/api/project/add", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "Registration unavailable. Try again." }) }), { times: 1 });
    // A refused registration reads at once: the overview stays unanswered until the error is on screen.
    let releaseOverview!: () => void;
    const overviewHeld = new Promise<void>((resolve) => { releaseOverview = resolve; });
    let overviewEntered!: () => void;
    const overviewStarted = new Promise<void>((resolve) => { overviewEntered = resolve; });
    await page.route("**/api/overview", async (route) => { overviewEntered(); await overviewHeld; await route.continue(); });
    // Force the overlapping browser read instead of depending on a poll during the screenshot.
    const overviewRead = page.evaluate(async () => (await fetch("/api/overview")).status);
    await overviewStarted;
    await row.getByRole("button", { name: "Add project", exact: true }).click();
    await walk.state("02-attach-error", { visible: [firstRun.getByRole("alert").filter({ hasText: "Registration unavailable" }), row.getByRole("button", { name: "Retry", exact: true })], hidden: [] });
    // Keep the handler: removing the last page route also continues its in-flight requests.
    releaseOverview();
    expect(await overviewRead).toBe(200);
    let release!: () => void;
    const wait = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/project/add", async (route) => { await wait; await route.continue(); }, { times: 1 });
    await row.getByRole("button", { name: "Retry", exact: true }).click();
    await walk.state("03-attach-pending", { visible: [firstRun.getByRole("status").filter({ hasText: "Adding project…" })], hidden: [firstRun.getByRole("alert")] });
    release();
    await expect(page).toHaveURL(`${service}/projects/sample-project?setup=1`);
    await page.getByRole("dialog", { name: "Project setup" }).getByRole("button", { name: "Open conversation" }).click();
    await expect.poll(async () => (await (await request.get(`${service}/api/chat/sample-project`)).json()).history.some((entry: { text: string }) => entry.text === "Queued request answered.")).toBe(true);
    await page.reload();
    await walk.state("04-attached-again", { visible: [page.getByText("Saved project history."), page.getByText("Queued request answered."), page.getByRole("textbox", { name: "Message L3 about sample-project" })], hidden: [firstRun, page.getByRole("list", { name: "Queued messages" })] });
    const project = await (await request.get(`${service}/api/project/sample-project`)).json();
    expect(project.l3.session_id).toBe("fixture-saved-session");
    expect(project.archive).toHaveLength(1);
  });
});
