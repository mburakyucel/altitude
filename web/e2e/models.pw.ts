import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/*
 * Model choice and settings by destination (SPEC.md §3.6, §3.10, §3.15), walked at 390 and 1440 against the
 * disposable service: the L3 button under the message box, New tasks beside the quota (desktop rail, phone
 * Monitor and Work), the Models dialog's states, the Settings overview groups, a project's settings sections,
 * the Remove project dialog and the task's model facts. Choices are saved and read back through the real API;
 * only the save failure and the task's reported model are overlays. Engine and model names come from the API.
 */

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
const starts = (label: string) => new RegExp(`^${label}`);

function views(page: Page, phone: boolean, project: string) {
  const main = page.getByRole("main");
  return {
    main,
    l3: main.getByRole("button", { name: /^L3 model: / }),
    // Desktop: the rail under the engine meters. Phone: the top of Work and Monitor.
    newTasks: (phone ? main : page.locator(".rail")).getByRole("button", { name: /^New tasks · / }),
    l3Dialog: page.getByRole("dialog", { name: `Models · L3 · ${project} only`, exact: true }),
    tasksDialog: page.getByRole("dialog", { name: "Models · Tasks · All projects", exact: true }),
  };
}

test("Models: closed controls, L3 and Tasks tabs, Other model, saving, failure, changed elsewhere, Use and Back to Auto", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const v = views(page, phone, project.name);
  const view = await options(request, project.name);
  const { engine, models: [first, second], efforts } = runnable(view);
  const top = efforts.at(-1)!;
  const high = efforts.find((e) => e.value === "high")!;
  const tab = (dialog: typeof v.l3Dialog, name: "L3" | "Tasks") => dialog.getByRole("tab", { name: starts(name) });
  const work = `${project.path}?tab=work`;

  await walk.open(project.path);
  await walk.state("01-closed-composer-l3", {
    visible: [v.l3, ...(phone ? [] : [v.newTasks])], hidden: [v.l3Dialog, v.tasksDialog, ...(phone ? [v.newTasks] : [])],
  });
  await expect(v.l3).toHaveText("L3 · Auto");
  if (!phone) await expect(v.newTasks).toHaveText("New tasks · Auto›");

  await walk.state("02-open-on-l3", {
    action: () => v.l3.click(),
    visible: [v.l3Dialog, v.l3Dialog.getByText(`In use: Auto · who answers you in ${project.name}'s chat.`), v.l3Dialog.getByText("Applies from L3's next reply.", { exact: false })],
    hidden: [v.l3Dialog.getByRole("button", { name: "Back to Auto" }), v.l3Dialog.getByRole("alert")],
  });
  await expect(tab(v.l3Dialog, "L3")).toHaveAttribute("aria-selected", "true");
  await expect(v.l3Dialog.getByRole("radio", { name: /^Auto/ })).toBeFocused();
  await expect(v.l3Dialog.getByRole("button", { name: `Use for L3 in ${project.name}`, exact: true })).toBeDisabled();

  // One draft belongs to the open tab: switching tabs drops it.
  await v.l3Dialog.getByRole("radio", { name: starts(second!.label) }).check();
  await expect(v.l3Dialog.getByRole("button", { name: `Use for L3 in ${project.name}`, exact: true })).toBeEnabled();
  await tab(v.l3Dialog, "L3").focus();
  await walk.state("03-tab-switch-to-tasks-drops-draft", {
    action: () => page.keyboard.press("ArrowRight"),
    visible: [v.tasksDialog, v.tasksDialog.getByText("For one task, tell L3:", { exact: false })], hidden: [v.l3Dialog],
  });
  await expect(tab(v.tasksDialog, "Tasks")).toBeFocused();
  await expect(v.tasksDialog.getByRole("radio", { name: /^Auto/ })).toBeChecked();
  await walk.state("04-tab-switch-back-to-l3", {
    action: () => page.keyboard.press("ArrowLeft"), visible: [v.l3Dialog], hidden: [v.tasksDialog],
  });
  await expect(v.l3Dialog.getByRole("radio", { name: /^Auto/ })).toBeChecked();
  await expect(v.l3Dialog.getByRole("radio", { name: starts(second!.label) })).not.toBeChecked();

  await v.l3Dialog.getByRole("radio", { name: starts(second!.label) }).check();
  await walk.state("05-l3-used", {
    action: () => v.l3Dialog.getByRole("button", { name: `Use for L3 in ${project.name}`, exact: true }).click(),
    visible: [v.l3], hidden: [v.l3Dialog],
  });
  await expect(v.l3).toHaveAccessibleName(`L3 model: ${second!.label}`);
  expect((await options(request, project.name)).l3_choice).toEqual({ engine, model: second!.model });

  if (phone) await walk.open(work);
  await walk.state("06-closed-new-tasks", { visible: [v.newTasks], hidden: [v.tasksDialog] });
  await expect(v.newTasks).toHaveText("New tasks · Auto›");
  await walk.state("07-open-on-tasks", {
    action: () => v.newTasks.click(),
    visible: [v.tasksDialog, v.tasksDialog.getByText("In use: Auto · every project; tasks that start from now, including queued ones. Started tasks keep theirs.")],
    hidden: [v.tasksDialog.getByRole("button", { name: "Back to Auto" })],
  });
  await expect(tab(v.tasksDialog, "Tasks")).toHaveAttribute("aria-selected", "true");

  const use = v.tasksDialog.getByRole("button", { name: "Use for all new tasks", exact: true });
  const modelId = v.tasksDialog.getByRole("textbox", { name: "Model id" });
  await v.tasksDialog.getByRole("radio", { name: "Other model…" }).check();
  await expect(v.tasksDialog.getByRole("combobox", { name: "Engine" })).toHaveValue(engine);
  await expect(use).toBeDisabled();
  await modelId.fill("fixture model");
  await expect(use).toBeDisabled();
  await modelId.fill("fixture-model-next");
  await walk.state("08-other-model-typed", {
    visible: [v.tasksDialog.getByRole("combobox", { name: "Engine" }), modelId, v.tasksDialog.getByText(`When fixture-model-next · ${view.engines[0]!.label} is unavailable, each project's Auto picks instead.`)],
    hidden: [v.tasksDialog.getByRole("alert")],
  });
  await expect(use).toBeEnabled();
  await v.tasksDialog.getByRole("radio", { name: high.label, exact: true }).check();

  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let refuse = true;
  await page.route("**/api/new-tasks", async (route) => {
    if (!refuse) return route.fallback();
    await gate;
    return route.fulfill({ status: 400, json: { error: "The fixture refuses this model." } });
  });
  try {
    await walk.state("09-saving", {
      action: () => use.click(),
      visible: [v.tasksDialog.getByRole("button", { name: "Saving…", exact: true })], hidden: [v.tasksDialog.getByRole("alert")],
    });
    await expect(v.tasksDialog.getByRole("button", { name: "Saving…", exact: true })).toBeDisabled();
    await expect(tab(v.tasksDialog, "L3")).toBeDisabled();
    await expect(modelId).toBeDisabled();
    release();
    await walk.state("10-save-failed-retry", {
      visible: [v.tasksDialog.getByRole("alert").filter({ hasText: "The fixture refuses this model." }), v.tasksDialog.getByRole("button", { name: "Retry", exact: true })],
      hidden: [v.tasksDialog.getByRole("button", { name: "Saving…" })],
    });
    await expect(modelId).toHaveValue("fixture-model-next");
    expect(await newTasks(request)).toBeNull();

    // Another window saves New tasks meanwhile; Retry then refuses instead of overwriting it.
    refuse = false;
    const elsewhere = { engine, model: second!.model!, effort: high.value };
    expect((await request.post("/api/new-tasks", { data: { value: elsewhere, expected: null } })).ok()).toBe(true);
    await walk.state("11-changed-elsewhere-reload", {
      action: () => v.tasksDialog.getByRole("button", { name: "Retry", exact: true }).click(),
      visible: [v.tasksDialog.getByRole("alert").filter({ hasText: "Changed in another window." }), v.tasksDialog.getByRole("button", { name: "Reload", exact: true })],
      hidden: [v.tasksDialog.getByRole("button", { name: "Retry" })],
    });
    expect(await newTasks(request)).toEqual(elsewhere);
    await walk.state("12-reloaded-current-choice", {
      action: () => v.tasksDialog.getByRole("button", { name: "Reload", exact: true }).click(),
      visible: [v.tasksDialog.getByText(`In use: ${second!.label} · ${high.label}`), v.tasksDialog.getByRole("button", { name: "Back to Auto" })],
      hidden: [v.tasksDialog.getByRole("alert"), modelId],
    });
    await expect(v.tasksDialog.getByRole("radio", { name: starts(second!.label) })).toBeChecked();
    await expect(v.tasksDialog.getByRole("radio", { name: high.label, exact: true })).toBeChecked();
  } finally { release(); }

  await v.tasksDialog.getByRole("radio", { name: starts(first!.label) }).check();
  await v.tasksDialog.getByRole("radio", { name: top.label, exact: true }).check();
  await walk.state("13-closed-after-use", { action: () => use.click(), visible: [v.newTasks], hidden: [v.tasksDialog] });
  await expect(v.newTasks).toHaveText(`New tasks · ${first!.label} · ${top.label}${phone ? " · every project" : ""}›`);
  expect(await newTasks(request)).toEqual({ engine, model: first!.model, effort: top.value });

  await v.newTasks.click();
  await walk.state("14-back-to-auto", {
    action: () => v.tasksDialog.getByRole("button", { name: "Back to Auto", exact: true }).click(),
    visible: [v.newTasks], hidden: [v.tasksDialog],
  });
  await expect(v.newTasks).toHaveText("New tasks · Auto›");
  expect(await newTasks(request)).toBeNull();

  // Outside a project only the Tasks tab exists: phone Monitor, and the desktop Settings row.
  if (phone) await walk.open("/monitor");
  else await walk.open("/settings");
  const opener = phone ? v.newTasks : v.main.getByRole("button", { name: /^New tasks / });
  await walk.state("15-tasks-only-outside-a-project", {
    action: () => opener.click(), visible: [v.tasksDialog], hidden: [v.tasksDialog.getByRole("tablist")],
  });
  await walk.state("16-closed-with-escape", { action: () => page.keyboard.press("Escape"), visible: [opener], hidden: [v.tasksDialog] });
  await expect(opener).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("Settings by destination: the menu, the overview groups, every project and one project's sections", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const menu = page.getByRole("menu", { name: "Project actions" });
  const group = (name: string) => main.getByRole("region", { name, exact: true });
  await walk.open(project.path);
  await walk.state("01-menu-without-remove", {
    action: () => page.getByRole("button", { name: "More actions" }).click(),
    visible: [menu.getByRole("menuitem", { name: "Project settings…", exact: true }), menu.getByRole("menuitem", { name: /^Setup: / }),
      menu.getByRole("menuitem", { name: "Reset L3 conversation…", exact: true }), menu.getByRole("menuitem", { name: "All settings…", exact: true })],
    hidden: [menu.getByText(/Remove/)],
  });
  await walk.state("02-settings-overview", {
    action: () => menu.getByRole("menuitem", { name: "All settings…", exact: true }).click(),
    visible: [main.getByRole("link", { name: /Your name|Not set/ }).first(), group("This project"), group("Models"), group("Projects"), group("Voice"),
      group("Devices and access"), group("Coding agents")],
    hidden: [menu, main.getByRole("heading", { name: "This machine" }), main.getByRole("link", { name: /^Projects folder / })],
  });
  await expect(main.getByRole("link").first()).toHaveAttribute("href", "/settings/name");
  await expect(group("This project").getByRole("link")).toHaveAttribute("href", `/settings/projects/${project.name}`);
  await expect(group("Models").getByRole("button", { name: "New tasks Auto · each project's own defaults", exact: true })).toBeEnabled();
  await expect(group("Devices and access").getByRole("link", { name: /^Devices / })).toHaveAttribute("href", "/settings/devices");
  await expect(group("Devices and access").getByRole("switch", { name: /Terminal/ })).toBeVisible();
  await expect(group("Devices and access").getByText(/ · HTTPS off$/)).toBeVisible();
  await expect(group("Coding agents").getByRole("switch", { name: /Validation runs/ })).toBeVisible();
  await expect(group("Coding agents").getByRole("link", { name: /^Prerequisites / })).toBeVisible();
  await expect(group("Coding agents").getByRole("link", { name: /^Incident reports / })).toBeVisible();

  await walk.state("03-all-projects", {
    action: () => group("Projects").getByRole("link", { name: /^All projects / }).click(),
    visible: [main.getByRole("link", { name: project.name, exact: true }), main.getByRole("link", { name: /^Projects folder / })],
    hidden: [group("Models")],
  });
  await walk.state("04-project-sections", {
    action: () => main.getByRole("link", { name: project.name, exact: true }).click(),
    visible: [group("L3"), group("Auto defaults"), group("Routing"), group("Project"),
      group("Auto defaults").getByRole("group", { name: /^Tasks · / }).first(), group("Auto defaults").getByRole("group", { name: /^L3 · / }).first(),
      group("Routing").getByRole("combobox", { name: "Tasks routing" }), group("Routing").getByRole("combobox", { name: "L3 routing" }),
      group("Project").getByRole("link", { name: /^Setup / }), group("Project").getByRole("button", { name: "Remove…", exact: true })],
    hidden: [main.getByRole("alert")],
  });
  await expect(group("Project").getByRole("link", { name: /^Setup / })).toHaveAttribute("href", `/projects/${project.name}?setup=1`);
  const defaults = await options(request, project.name);
  for (const row of ["Tasks", "L3"]) for (const engine of defaults.engines) {
    const fields = group("Auto defaults").getByRole("group", { name: `${row} · ${engine.label}`, exact: true });
    await expect(fields.getByLabel("Model")).toBeVisible();
    await expect(fields.getByLabel("Effort")).toBeVisible();
  }
  await walk.state("05-change-opens-models-on-l3", {
    action: () => group("L3").getByRole("button", { name: "Change…", exact: true }).click(),
    visible: [page.getByRole("dialog", { name: `Models · L3 · ${project.name} only`, exact: true })], hidden: [],
  });
  await page.keyboard.press("Escape");
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

test("the task's model: requested before the engine reports, then reported, and Model and effort in details", async ({ page, request }, info) => {
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
  await page.route((url) => url.pathname === `/api/task/${project.name}/${slug}`, (route) => route.fulfill({ json: {
    ...record, launch_model: first!.model, launch_effort: top.value, engine_model: first!.model, engine_reasoning_effort: reported,
    model: first!.model, effort: top.value, routing: "set for this task by L3",
  } }));
  const chip = (text: RegExp) => page.locator(".task-chips").getByText(text).first();
  const details = page.getByRole("dialog", { name: "Task details", exact: true });
  const facts = details.getByRole("region", { name: "Model and effort", exact: true });
  const requested = new RegExp(`^Requested · ${first!.label}\\b.* on ${engineLabel} · ${top.label}$`);
  const mismatch = new RegExp(`^${first!.label}\\b.* on ${engineLabel} · ${high.label} \\(requested ${top.label}\\)$`);

  await walk.open(`/projects/${project.name}/tasks/${slug}`);
  await page.getByRole("button", { name: /Task details$/ }).click();
  await walk.state("01-requested-before-report", {
    visible: [details, chip(requested), facts, facts.getByText(`${first!.model} · ${top.label}`, { exact: true }), facts.getByText("model not reported · effort not reported", { exact: true })],
    hidden: [chip(mismatch)],
  });
  await expect(facts.getByRole("term")).toHaveText(["Requested", "Launched", "Engine reports", "Routing"]);
  await expect(facts.getByText(`${first!.model} · ${top.label} · set for this task`, { exact: true })).toBeVisible();
  await expect(facts.getByText("Set for this task by L3", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");

  reported = high.value;
  await page.reload();
  if (!phone) await expect(chip(mismatch)).toBeVisible();
  await page.getByRole("button", { name: /Task details$/ }).click();
  await walk.state("02-reported-differs", {
    visible: [chip(mismatch), facts.getByText(`model not reported · ${high.label}`, { exact: true }), facts.getByText(/^The engine reported a different level\./)],
    hidden: [chip(requested)],
  });
});
