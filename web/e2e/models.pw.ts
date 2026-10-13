import { expect, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

test.use({ deviceScaleFactor: 2 });

// Settings persist through the real API; route overlays only force boundary failures.
type Choice = { engine?: string; model?: string | null; effort?: string } | null;
type Options = {
  models: { engine: string; model: string | null; label: string }[];
  efforts: Record<string, { value: string; label: string }[]>;
  engines: { value: string; label: string }[];
};

async function options(request: APIRequestContext, project: string) {
  return await (await request.get(`/api/defaults/${project}`)).json() as Options & { l3_choice: Choice };
}
/** The listed models of the engine the fixture can run, so a choice is in use rather than unavailable. */
function runnable(view: Options) {
  const engine = view.engines[0]!.value;
  const models = view.models.filter((row) => row.engine === engine && row.model);
  expect(models.length, "The walkthrough needs two listed models on one engine").toBeGreaterThan(1);
  return { engine, models, efforts: view.efforts[engine]! };
}

type Rows = { rows: { id: string; label: string; detail: string }[] };
const settingsRows = async (request: APIRequestContext): Promise<Rows["rows"]> => ((await (await request.get("/api/project-model-settings")).json()) as Rows).rows;

test("project settings: listed only when a project has its own, Reset with pending, failure and retry", async ({ page, request }, info) => {
  const walk = walkthrough(page, info); const project = await fixtureProject(request);
  const view = await options(request, project.name);
  const { engine } = runnable(view);
  const label = `${project.name} · Tasks`;
  const section = page.getByRole("region", { name: "Project settings", exact: true });
  await walk.open("/settings");
  await walk.state("01-none-no-section", { visible: [page.getByRole("region", { name: "Projects", exact: true })], hidden: [section, page.getByRole("combobox")] });
  const roles = (await (await request.get(`/api/defaults/${project.name}`)).json()).roles as { role: string; engines: { engine: string; effort: { setting: string } }[] }[];
  const setting = roles.find((row) => row.role === "l2")!.engines.find((row) => row.engine === engine)!.effort.setting;
  expect((await request.post("/api/defaults", { data: { project: project.name, setting, value: "high", expected: null } })).ok()).toBe(true);
  await page.reload();
  const row = section.getByRole("group", { name: label, exact: true });
  await walk.state("02-listed", { visible: [section, row, row.getByText(`${view.engines[0]!.label} · High`, { exact: true }), row.getByRole("button", { name: "Reset", exact: true })], hidden: [section.getByRole("alert")] });
  let finish!: () => void; const pending = new Promise<void>((resolve) => { finish = resolve; });
  await page.route("**/api/project-model-settings/reset", async (route) => { await pending; await route.fulfill({ status: 503, json: { error: "Fixture reset failed." } }); });
  try {
    await row.getByRole("button", { name: "Reset", exact: true }).click();
    await walk.state("03-reset-pending", { visible: [row, row.getByRole("status", { name: "Resetting…", exact: true })], hidden: [row.getByRole("alert")] });
    finish();
    await walk.state("04-reset-failed-keeps-setting", { visible: [row, row.getByRole("alert").filter({ hasText: "Fixture reset failed." }), row.getByRole("button", { name: "Retry", exact: true })], hidden: [row.getByRole("status", { name: "Resetting…", exact: true })] });
    expect((await settingsRows(request)).map((item) => item.label)).toEqual([label]);
  } finally { finish(); await page.unroute("**/api/project-model-settings/reset"); }
  await row.getByRole("button", { name: "Retry", exact: true }).click();
  await expect.poll(() => settingsRows(request)).toEqual([]);
  await walk.state("05-reset-removes-section", { visible: [page.getByRole("heading", { level: 1, name: "Settings" })], hidden: [section] });
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test.describe("Remove project", () => {
  test.use({ scenario: "lifecycle" });
  test("the dialog: Cancel focused, refusal, removing, removed", async ({ page, request, service }, info) => {
    const walk = walkthrough(page, info);
    const main = page.getByRole("main");
    const remove = main.getByRole("region", { name: "Project", exact: true }).getByRole("button", { name: "Remove…", exact: true });
    const dialog = (name: string) => page.getByRole("dialog", { name: `Remove ${name} from Altitude?`, exact: true });

    await walk.open(`${service}/settings/projects/busy-project`);
    const busy = dialog("busy-project");
    await walk.state("01-open-cancel-focused", {
      action: () => remove.click(),
      visible: [busy, busy.getByText(/Stays on disk/), busy.getByText(/Not kept: its settings here/), busy.getByText(/Undo: add the same folder/),
        busy.getByRole("button", { name: "Remove busy-project", exact: true })],
      hidden: [busy.getByRole("alert"), busy.getByRole("status")],
    });
    await expect(busy.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
    await walk.state("02-refused", {
      action: () => busy.getByRole("button", { name: "Remove busy-project", exact: true }).click(),
      visible: [busy.getByRole("alert").filter({ hasText: /existing-work/ }), busy.getByRole("button", { name: "Remove busy-project", exact: true })],
      hidden: [busy.getByRole("status")],
    });
    expect((await (await request.get(`${service}/api/overview`)).json()).projects.find((row: { name: string }) => row.name === "busy-project").managed).toBe(true);

    await walk.open(`${service}/settings/projects/sample-project`);
    const sample = dialog("sample-project");
    await remove.click();
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/project/remove", async (route) => { await held; await route.continue(); }, { times: 1 });
    try {
      await walk.state("03-removing", {
        action: () => sample.getByRole("button", { name: "Remove sample-project", exact: true }).click(),
        visible: [sample.getByRole("button", { name: "Removing…", exact: true })],
        hidden: [sample.getByRole("alert")],
      });
      await expect(sample.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
      await expect(sample.getByRole("button", { name: "Removing…", exact: true })).toBeDisabled();
    } finally { release(); }
    await expect(page).toHaveURL(`${service}/`);
    await walk.state("04-removed", { visible: [page.getByRole("heading", { name: "Needs you", exact: true })], hidden: [sample] });
    expect((await (await request.get(`${service}/api/overview`)).json()).projects.find((row: { name: string }) => row.name === "sample-project").managed).toBe(false);
  });
});

test("the task's model: launched before the engine reports, then reported, and Model and effort in details", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const view = await options(request, project.name);
  const { engine, models: [first], efforts } = runnable(view);
  const top = efforts.at(-1)!;
  const high = efforts.find((e) => e.value === "high")!;
  const engineLabel = view.engines[0]!.label;
  const tasks = (await (await request.get(`/api/project/${project.name}`)).json()).tasks as { slug: string; state: string }[];
  const slug = tasks.find((row) => row.state === "running")!.slug;
  const record = await (await request.get(`/api/task/${project.name}/${slug}`)).json();
  expect(record.l2_engine).toBe(engine);
  // Presentation overlay: the fixture's dispatch predates model choice, so the launch facts are served here.
  let reported: string | null = null;
  const launched = `${first!.label} · ${top.label}`;
  await page.route((url) => url.pathname === `/api/task/${project.name}/${slug}`, (route) => route.fulfill({ json: {
    ...record, launch_model: first!.model, launch_effort: top.value, engine_model: first!.model, engine_reasoning_effort: reported,
    model: first!.model, effort: top.value, routing: "set for this task by L3",
    running_model: { engine: engineLabel, model: first!.label, effort: reported ? high.label : top.label,
      chosen: launched, launched, reported: reported ? `${first!.label} · ${high.label}` : null },
  } }));
  const chip = (text: RegExp) => page.locator(".task-chips").getByText(text).first();
  const details = page.getByRole("dialog", { name: "Task details", exact: true });
  const facts = details.getByRole("region", { name: "Model and effort", exact: true });
  const requested = new RegExp(`^${first!.label}\\b.* on ${engineLabel} · ${top.label}$`);
  const reportedChip = new RegExp(`^${first!.label}\\b.* on ${engineLabel} · ${high.label}$`);

  await walk.open(`/projects/${project.name}/tasks/${slug}`);
  await page.getByRole("button", { name: /Task details$/ }).click();
  await walk.state("01-requested-before-report", {
    visible: [details, chip(requested), facts, facts.getByText(launched, { exact: true }), facts.getByText("Not reported yet", { exact: true })],
    hidden: [chip(reportedChip)],
  });
  await expect(facts.getByRole("term")).toHaveText(["Requested", "Launched", "Engine reports", "Routing"]);
  await expect(facts.getByText(`${launched} · set for this task`, { exact: true })).toBeVisible();
  await expect(facts.getByText("Set for this task by L3", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");

  reported = high.value;
  await page.reload();
  if (!phone) await expect(chip(reportedChip)).toBeVisible();
  await page.getByRole("button", { name: /Task details$/ }).click();
  await walk.state("02-reported", {
    visible: [chip(reportedChip), facts.getByText(`${first!.label} · ${high.label}`, { exact: true }), facts.getByText(`Messages and resumes ask for ${launched}.`, { exact: true })],
    hidden: [chip(requested)],
  });
});
