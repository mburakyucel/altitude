import { expect, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

test.use({ deviceScaleFactor: 2 });

// Global pairs persist through the real API; route overlays only force boundary failures.
type Choice = { engine?: string; model?: string | null; effort?: string } | null;
type Options = {
  models: { engine: string; model: string | null; label: string }[];
  efforts: Record<string, { value: string; label: string }[]>;
  engines: { value: string; label: string }[];
};

async function options(request: APIRequestContext, project: string) {
  return await (await request.get(`/api/defaults/${project}`)).json() as Options & { l3_choice: Choice };
}
async function newTasks(request: APIRequestContext) {
  return (await (await request.get("/api/overview")).json()).new_tasks.value as Choice;
}
/** The listed models of the engine the fixture can run, so a choice is in use rather than unavailable. */
function runnable(view: Options) {
  const engine = view.engines[0]!.value;
  const models = view.models.filter((row) => row.engine === engine && row.model);
  expect(models.length, "The walkthrough needs two listed models on one engine").toBeGreaterThan(1);
  return { engine, models, efforts: view.efforts[engine]! };
}

type Pair = { model: string | null; effort: string | null };
type GlobalView = { engines: { engine: string; label: string; value: Pair;
  models: { value: string; label: string }[]; efforts: { value: string; label: string }[];
  default_model: string; default_effort: string; native_effort: string; unavailable: string | null }[];
  overrides: { id: string; label: string; expected: unknown }[] };
const globalView = async (request: APIRequestContext): Promise<GlobalView> => (await request.get("/api/model-defaults")).json();
const pair = async (request: APIRequestContext, engine: string) => (await globalView(request)).engines.find((row) => row.engine === engine)!.value;
const key = (engine: string, model: string) => JSON.stringify([engine, model]);

test("global model and effort pairs: named choices, independent saves, Native and inheritance", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const initial = await globalView(request);
  await walk.open("/settings");
  const section = page.getByRole("region", { name: "Models and effort · All projects", exact: true });
  await walk.state("01-global-inherited-pairs", { visible: [section], hidden: [section.getByRole("alert")] });
  for (const row of initial.engines) {
    const model = section.getByRole("combobox", { name: `${row.label} model`, exact: true });
    const effort = section.getByRole("combobox", { name: `${row.label} effort`, exact: true });
    await expect(model).toHaveValue(""); await expect(effort).toHaveValue("");
    for (const item of row.models) {
      await model.selectOption(key(row.engine, item.value));
      await expect.poll(() => pair(request, row.engine)).toEqual({ model: item.value, effort: null });
    }
    const selected = row.models.at(-1)!;
    await effort.selectOption("high");
    await expect.poll(() => pair(request, row.engine)).toEqual({ model: selected.value, effort: "high" });
    await model.selectOption(key(row.engine, row.models[0]!.value));
    await expect.poll(() => pair(request, row.engine)).toEqual({ model: row.models[0]!.value, effort: "high" });
  }
  await walk.state("02-explicit-pairs", { visible: [section, section.getByText(/Access unverified/).first()], hidden: [section.getByRole("alert")] });
  await page.reload();
  for (const row of initial.engines) {
    await expect(section.getByRole("combobox", { name: `${row.label} model`, exact: true })).toHaveValue(key(row.engine, row.models[0]!.value));
    await expect(section.getByRole("combobox", { name: `${row.label} effort`, exact: true })).toHaveValue("high");
  }
  const row = initial.engines[0]!;
  const effort = section.getByRole("combobox", { name: `${row.label} effort`, exact: true });
  await effort.selectOption("native");
  await expect.poll(() => pair(request, row.engine)).toEqual({ model: row.models[0]!.value, effort: "native" });
  await walk.state("03-explicit-native", { visible: [section.getByText(row.native_effort, { exact: true })], hidden: [section.getByRole("alert")] });
  const other = initial.engines[1]!;
  expect(await pair(request, other.engine)).toEqual({ model: other.models[0]!.value, effort: "high" });
  await section.getByRole("combobox", { name: `${row.label} model`, exact: true }).selectOption("");
  await expect.poll(() => pair(request, row.engine)).toEqual({ model: null, effort: "native" });
  await effort.selectOption("");
  await expect.poll(() => pair(request, row.engine)).toEqual({ model: null, effort: null });
  await walk.state("04-inheritance-restored", { visible: [section.getByText(row.default_effort, { exact: true })], hidden: [section.getByRole("alert")] });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("global pairs: custom ID, atomic pending pair, denied save and stale draft", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const initial = await globalView(request); const row = initial.engines[0]!, other = initial.engines[1]!;
  const group = page.getByRole("group", { name: row.label, exact: true });
  const model = group.getByRole("combobox", { name: `${row.label} model`, exact: true });
  const effort = group.getByRole("combobox", { name: `${row.label} effort`, exact: true });
  const id = group.getByRole("textbox", { name: "Model ID", exact: true });
  await walk.open("/settings");
  await model.selectOption("other"); await id.fill("invalid model"); await id.press("Enter");
  await walk.state("01-invalid-custom", { visible: [id, group.getByRole("alert").filter({ hasText: "without spaces" })], hidden: [] });
  await id.fill("fictional-model"); await id.press("Enter");
  await expect.poll(() => pair(request, row.engine)).toEqual({ model: "fictional-model", effort: null });
  await walk.state("02-custom-saved", { visible: [model], hidden: [id, group.getByRole("alert")] });
  await expect(model).toBeFocused();
  let release!: () => void; const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/model-defaults", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await gate; await route.fulfill({ status: 403, json: { error: "Fixture selection denied." } });
  });
  try {
    await effort.selectOption("high");
    await expect(model).toHaveAttribute("aria-disabled", "true"); await expect(effort).toHaveAttribute("aria-disabled", "true");
    await expect(page.getByRole("combobox", { name: `${other.label} model`, exact: true })).toHaveAttribute("aria-disabled", "false");
    await walk.state("03-pair-pending", { visible: [group.getByRole("status", { name: "Saving…", exact: true })], hidden: [group.getByRole("alert")] });
    release();
    await walk.state("04-denied-restores-pair", { visible: [group.getByRole("alert").filter({ hasText: "Fixture selection denied." })], hidden: [group.getByRole("button", { name: "Retry save" })] });
    await expect(effort).toHaveValue(""); expect(await pair(request, row.engine)).toEqual({ model: "fictional-model", effort: null });
  } finally { release(); await page.unroute("**/api/model-defaults"); }
  await model.selectOption("other"); await id.fill("stale-model");
  expect((await request.post("/api/model-defaults", { data: { engine: row.engine, value: { model: "elsewhere-model", effort: "high" }, expected: { model: "fictional-model", effort: null } } })).ok()).toBe(true);
  await id.press("Enter");
  await walk.state("05-stale-pair-refused", { visible: [id, group.getByRole("alert").filter({ hasText: "Changed in another window." })], hidden: [] });
  await group.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(model).toHaveValue(key(row.engine, "elsewhere-model")); await expect(effort).toHaveValue("high");
  await walk.state("06-reload-clears-stale-draft", { visible: [model], hidden: [id, group.getByRole("alert")] });
  await model.selectOption("other"); await id.fill("retry-custom-model");
  let finish!: () => void; const pending = new Promise<void>((resolve) => { finish = resolve; });
  await page.route("**/api/model-defaults", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await pending; await route.fulfill({ status: 503, json: { error: "Fixture custom save failed." } });
  });
  try {
    await id.press("Enter");
    await walk.state("07-custom-pending", { visible: [id, group.getByRole("status", { name: "Saving…", exact: true })], hidden: [group.getByRole("alert")] });
    await expect(id).toHaveAttribute("readonly", "");
    finish();
    await walk.state("08-custom-failed-draft-retained", { visible: [id, group.getByRole("alert").filter({ hasText: "Fixture custom save failed." })], hidden: [group.getByRole("status", { name: "Saving…", exact: true })] });
    await expect(id).toHaveValue("retry-custom-model");
    expect(await pair(request, row.engine)).toEqual({ model: "elsewhere-model", effort: "high" });
  } finally { finish(); await page.unroute("**/api/model-defaults"); }
  await group.getByRole("button", { name: "Retry save" }).click();
  await expect.poll(() => pair(request, row.engine)).toEqual({ model: "retry-custom-model", effort: "high" });
  await walk.state("09-custom-retry-success", { visible: [model], hidden: [id, group.getByRole("alert")] });
});

test("global pair read states and recoverable save failure", async ({ page, request }, info) => {
  const walk = walkthrough(page, info); const row = (await globalView(request)).engines[0]!;
  const section = page.getByRole("region", { name: "Models and effort · All projects", exact: true });
  const model = section.getByRole("combobox", { name: `${row.label} model`, exact: true });
  let release!: () => void; const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/model-defaults", async (route) => { await gate; await route.fulfill({ status: 503, json: { error: "Fixture reading unavailable." } }); });
  try {
    await walk.open("/settings");
    await walk.state("01-loading", { visible: [section.getByRole("status", { name: "Loading models…", exact: true })], hidden: [model] });
    release();
    await walk.state("02-read-error", { visible: [section.getByRole("alert")], hidden: [model] });
  } finally { release(); await page.unroute("**/api/model-defaults"); }
  await section.getByRole("button", { name: "Retry", exact: true }).click();
  await walk.state("03-read-recovered", { visible: [model], hidden: [section.getByRole("alert")] });
  await page.route("**/api/model-defaults", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({ status: 503, json: { error: "Fixture save unavailable." } });
  });
  await model.selectOption(key(row.engine, row.models[0]!.value));
  await walk.state("04-save-error", { visible: [section.getByRole("alert"), section.getByRole("button", { name: "Retry save" })], hidden: [] });
  await expect(model).toHaveValue("");
  await page.unroute("**/api/model-defaults"); await section.getByRole("button", { name: "Retry save" }).click();
  await expect.poll(() => pair(request, row.engine)).toEqual({ model: row.models[0]!.value, effort: null });
  await walk.state("05-save-recovered", { visible: [model], hidden: [section.getByRole("alert")] });
  await page.route("**/api/model-defaults", async (route) => {
    const response = await route.fetch(); const data = await response.json(); data.engines[0].unavailable = "Fixture model access rejected.";
    await route.fulfill({ response, json: data });
  });
  await page.reload();
  await walk.state("06-unavailable-keeps-request", { visible: [section.getByText("Unavailable · Fixture model access rejected.")], hidden: [section.getByRole("alert")] });
  await expect(model).toHaveValue(key(row.engine, row.models[0]!.value));
});

test("explicit existing override clears without changing engine or global pair", async ({ page, request }, info) => {
  const walk = walkthrough(page, info); const project = await fixtureProject(request);
  const row = (await globalView(request)).engines[0]!;
  const value = { engine: row.engine, model: "existing-model", effort: "high" };
  expect((await request.post("/api/new-tasks", { data: { value, expected: null } })).ok()).toBe(true);
  await walk.open("/settings");
  const override = page.getByRole("group", { name: "New tasks · All projects", exact: true });
  await walk.state("01-existing-override", { visible: [override, override.getByRole("button", { name: "Use global defaults" })], hidden: [] });
  let finish!: () => void; const pending = new Promise<void>((resolve) => { finish = resolve; });
  await page.route("**/api/model-defaults/reset", async (route) => {
    await pending; await route.fulfill({ status: 503, json: { error: "Fixture reset failed." } });
  });
  try {
    await override.getByRole("button", { name: "Use global defaults" }).click();
    await walk.state("02-reset-pending", { visible: [override, override.getByRole("status", { name: "Saving…", exact: true })], hidden: [override.getByRole("alert")] });
    finish();
    await walk.state("03-reset-failed-keeps-override", { visible: [override, override.getByRole("alert")], hidden: [override.getByRole("status", { name: "Saving…", exact: true })] });
    expect(await newTasks(request)).toEqual(value);
  } finally { finish(); await page.unroute("**/api/model-defaults/reset"); }
  await override.getByRole("button", { name: "Retry save" }).click();
  await expect.poll(() => newTasks(request)).toEqual({ engine: row.engine });
  await walk.state("04-override-removed", { visible: [page.getByRole("heading", { name: "Models and effort · All projects" })], hidden: [override] });
  expect(await pair(request, row.engine)).toEqual({ model: null, effort: null });
  await walk.open(`/settings/projects/${project.name}`);
  await walk.state("05-project-keeps-routing", { visible: [page.getByRole("combobox", { name: "Coordinator engine" }), page.getByRole("combobox", { name: "Tasks routing" })], hidden: [page.getByRole("heading", { name: "Auto defaults" })] });
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
