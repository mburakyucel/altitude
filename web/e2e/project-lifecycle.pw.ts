import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { expect, test as base } from "@playwright/test";
import { walkthrough } from "./walkthrough";

// Every mutation targets this test's disposable home and OS-selected loopback port, never UI_BASE_URL.
const test = base.extend<{ service: string; single: boolean }>({
  single: [false, { option: true }],
  service: async ({ single }, use) => {
    const child = spawn("python3", ["e2e/project-lifecycle-service.py", ...(single ? ["single"] : [])], { stdio: ["ignore", "pipe", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (data) => { stderr += data; });
    const lines = createInterface({ input: child.stdout });
    try {
      const ready = await Promise.race([
        once(lines, "line"),
        once(child, "exit").then(() => { throw new Error(`Disposable service exited: ${stderr}`); }),
        new Promise<never>((_, reject) => { const timer = setTimeout(() => reject(new Error("Disposable service did not start")), 10_000); timer.unref(); }),
      ]);
      const data = JSON.parse(ready[0] as string);
      expect(data.disposable).toBe(true);
      expect(new URL(data.url).hostname).toBe("127.0.0.1");
      await use(data.url as string);
    } finally {
      lines.close();
      child.kill("SIGTERM");
      if (child.exitCode === null) await once(child, "exit");
    }
  },
});

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
    const row = firstRun.getByRole("listitem").filter({ hasText: "sample-project" });
    await walk.state("01-last-project-removed", { visible: [firstRun, row.getByRole("button", { name: "Start L3", exact: true })], hidden: [page.getByRole("button", { name: "More actions" })] });
    expect(await page.evaluate(() => localStorage.getItem("altitude.project"))).toBeNull();
    const overview = await (await request.get(`${service}/api/overview`)).json();
    expect(overview.projects[0].managed).toBe(false);
    await page.route("**/api/project/add", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "Registration unavailable. Try again." }) }), { times: 1 });
    await row.getByRole("button", { name: "Start L3", exact: true }).click();
    await walk.state("02-attach-error", { visible: [firstRun.getByRole("alert").filter({ hasText: "Registration unavailable" }), row.getByRole("button", { name: "Retry", exact: true })], hidden: [] });
    let release!: () => void;
    const wait = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/project/add", async (route) => { await wait; await route.continue(); }, { times: 1 });
    await row.getByRole("button", { name: "Retry", exact: true }).click();
    await walk.state("03-attach-pending", { visible: [firstRun.getByRole("status").filter({ hasText: "L3 is starting…" })], hidden: [firstRun.getByRole("alert")] });
    release();
    await expect(page).toHaveURL(`${service}/projects/sample-project`);
    await expect.poll(async () => (await (await request.get(`${service}/api/chat/sample-project`)).json()).history.some((entry: { text: string }) => entry.text === "Queued request answered.")).toBe(true);
    await page.reload();
    await walk.state("04-attached-again", { visible: [page.getByText("Saved project history."), page.getByText("Queued request answered."), page.getByRole("textbox", { name: "Message L3 about sample-project" })], hidden: [firstRun, page.getByRole("list", { name: "Queued messages" })] });
    const project = await (await request.get(`${service}/api/project/sample-project`)).json();
    expect(project.l3.session_id).toBe("fixture-saved-session");
    expect(project.archive).toHaveLength(1);
  });
});
