import { expect, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

type Field = { setting: string; value: string | null; default: string };
type Choice = { engine?: string; model?: string; effort?: string } | null;
type Defaults = {
  l3_engine: string | null; l2_engine: string | null; l2_preference: string | null; l3_choice: Choice;
  engines: { value: string; label: string }[];
  models: { engine: string; model: string | null; label: string }[];
  roles: { role: string; engines: { engine: string; label: string; model: Field; effort: Field & { choices: { value: string; label: string }[] } }[] }[];
};

async function defaults(request: APIRequestContext, name: string) {
  const response = await request.get(`/api/defaults/${encodeURIComponent(name)}`);
  expect(response.ok()).toBe(true);
  return await response.json() as Defaults;
}
/** Every saved value keyed by its setting; engine names come from the API, never this file. */
const values = (view: Defaults) => Object.fromEntries(view.roles.flatMap((row) => row.engines.flatMap((e) =>
  [[e.model.setting, e.model.value], [e.effort.setting, e.effort.value]])));
const role = (view: Defaults, name: "l2" | "l3") => view.roles.find((row) => row.role === name)!;

test("Settings holds project models and effort; the menu reaches them and the draft survives", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const initial = await defaults(request, project.name);
  const [first, second] = role(initial, "l3").engines;
  const l2First = role(initial, "l2").engines[0]!;
  const draft = main.getByRole("textbox", { name: /^Message L3 about /, includeHidden: true });
  const menu = page.getByRole("menu", { name: "Project actions" });
  const row = main.getByRole("region", { name: "This project", exact: true }).getByRole("link", { name: new RegExp(`^${project.name} `) });
  const l3First = main.getByRole("group", { name: `L3 · ${first!.label}`, exact: true });
  const l3Second = main.getByRole("group", { name: `L3 · ${second!.label}`, exact: true });
  const l2Group = main.getByRole("group", { name: `Tasks · ${l2First.label}`, exact: true });
  const section = (name: string) => main.getByRole("region", { name, exact: true });
  /** Each field reports its own save, so a group can show two statuses at once. */
  const saved = (label: string) => l3First.locator(".default-field", { has: page.getByLabel(label) }).getByRole("status", { name: "Saved.", exact: true });

  await walk.open(project.path);
  await draft.fill("Keep my project draft");
  await walk.state("01-menu-links-to-settings", {
    action: () => page.getByRole("button", { name: "More actions" }).click(),
    visible: [menu, menu.getByRole("menuitem", { name: "Project settings…", exact: true }), menu.getByRole("menuitem", { name: "All settings…", exact: true })],
    hidden: [page.getByRole("dialog")],
  });
  await expect(menu.getByRole("combobox")).toHaveCount(0);
  await walk.state("02-this-project-row", {
    action: () => menu.getByRole("menuitem", { name: "All settings…", exact: true }).click(),
    visible: [row, section("Models"), section("Devices and access")], hidden: [menu, l3First],
  });
  await expect(row).toContainText("L3: Auto · routing Auto");
  await walk.state("03-project-settings", {
    action: () => row.click(),
    visible: [section("L3"), section("Auto defaults"), section("Routing"), section("Project"), l3First, l3Second, l2Group,
      section("L3").getByText(/last reply reported /), main.getByRole("combobox", { name: "L3 routing" })],
    hidden: [row],
  });
  // Tasks come before L3 in the Auto defaults table.
  await expect(main.getByRole("group", { name: /^(Tasks|L3) · / }).first()).toHaveAccessibleName(`Tasks · ${l2First.label}`);
  await expect(l3First.getByLabel("Effort")).toHaveValue("");
  await expect(l3First.getByLabel("Model")).toHaveAttribute("placeholder", `Default: ${first!.model.default}`);
  await expect(l2Group.getByLabel("Effort")).toHaveValue("");

  const choice = first!.effort.choices.at(-1)!;
  await walk.state("04-l3-effort-saved", {
    action: () => l3First.getByLabel("Effort").selectOption(choice.value),
    visible: [saved("Effort")], hidden: [l3Second.getByRole("status", { name: "Saved.", exact: true }), l2Group.getByRole("status", { name: "Saved.", exact: true })],
  });
  await l3First.getByLabel("Model").fill("fixture-model");
  await walk.state("05-model-saved-on-enter", {
    action: () => l3First.getByLabel("Model").press("Enter"),
    visible: [saved("Model")], hidden: [l3First.getByRole("alert")],
  });
  await expect(l3First.getByLabel("Model")).toHaveValue("fixture-model");
  expect(values(await defaults(request, project.name))).toEqual({
    ...values(initial), [first!.effort.setting]: choice.value, [first!.model.setting]: "fixture-model",
  });

  await walk.state("06-back-to-settings", {
    action: () => main.getByRole("link", { name: "‹ Settings", exact: true }).click(), visible: [row], hidden: [l3First],
  });
  if (info.project.name === "phone") await main.getByRole("button", { name: "‹ Back", exact: true }).click();
  else await page.getByRole("link", { name: project.name, exact: true }).click();
  await expect(draft).toHaveValue("Keep my project draft");

  await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
  await walk.state("07-persisted-on-direct-visit", { visible: [l3First], hidden: [saved("Model"), saved("Effort")] });
  await expect(l3First.getByLabel("Effort")).toHaveValue(choice.value);
  await expect(l3First.getByLabel("Model")).toHaveValue("fixture-model");
  await expect(l3Second.getByLabel("Effort")).toHaveValue("");
  await l3First.getByLabel("Model").fill("");
  await l3First.getByLabel("Effort").focus();
  await expect(l3First.getByLabel("Model")).toHaveValue("");
  await l3First.getByLabel("Effort").selectOption("");
  await walk.state("08-default-restored", { visible: [saved("Model"), saved("Effort")], hidden: [] });
  await expect.poll(async () => values(await defaults(request, project.name))).toEqual(values(initial));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("the L3 choice and L3 routing save from Settings, read back and return to Auto", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const initial = await defaults(request, project.name);
  const engine = initial.engines[0]!;
  const model = initial.models.find((row) => row.engine === engine.value && row.model)!;
  const l3 = main.getByRole("region", { name: "L3", exact: true });
  const routing = main.getByRole("combobox", { name: "L3 routing" });
  const dialog = page.getByRole("dialog", { name: `Models · L3 · ${project.name} only`, exact: true });
  const back = l3.getByRole("button", { name: "Back to Auto", exact: true });
  await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
  await walk.state("01-auto", { visible: [l3.getByText("Auto", { exact: true }), l3.getByRole("button", { name: "Change…", exact: true }), routing], hidden: [back, dialog] });
  await expect(routing).toHaveValue("");
  await expect(routing.locator("option")).toHaveText(["Auto", ...initial.engines.map((e) => `Only ${e.label}`)]);

  await walk.state("02-change-opens-models-on-l3", {
    action: () => l3.getByRole("button", { name: "Change…", exact: true }).click(),
    visible: [dialog, dialog.getByRole("tab", { name: /^L3/, selected: true })], hidden: [],
  });
  await dialog.getByRole("radio", { name: new RegExp(`^${model.label}`) }).check();
  await walk.state("03-l3-choice-saved", {
    action: () => dialog.getByRole("button", { name: `Use for L3 in ${project.name}`, exact: true }).click(),
    visible: [l3.getByText(model.label, { exact: true }), back], hidden: [dialog],
  });
  expect((await defaults(request, project.name)).l3_choice).toEqual({ engine: model.engine, model: model.model });
  await page.reload();
  await expect(l3.getByText(model.label, { exact: true })).toBeVisible();

  await walk.state("04-l3-routing-only", {
    action: () => routing.selectOption(`only:${engine.value}`),
    visible: [routing], hidden: [main.getByRole("region", { name: "Routing", exact: true }).getByRole("alert")],
  });
  await expect.poll(async () => (await defaults(request, project.name)).l3_engine).toBe(engine.value);
  await page.reload();
  await expect(routing).toHaveValue(`only:${engine.value}`);
  await routing.selectOption("");
  await expect.poll(async () => (await defaults(request, project.name)).l3_engine).toBeNull();

  await walk.state("05-back-to-auto", {
    action: () => back.click(), visible: [l3.getByText("Auto", { exact: true })], hidden: [back],
  });
  expect((await defaults(request, project.name)).l3_choice).toBeNull();
});

test("Tasks routing Prefer and Only save, survive reload, leave L3 alone and restore Auto", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const { engines } = await defaults(request, project.name);
  const preferred = engines[0]!;
  const card = main.getByRole("region", { name: "Routing", exact: true });
  const select = card.getByRole("combobox", { name: "Tasks routing" });
  const l3 = card.getByRole("combobox", { name: "L3 routing" });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let deny = false;
  await page.route("**/api/defaults", async (route) => {
    if (!deny) return route.fallback();
    await gate;
    return route.fulfill({ status: 403, json: { error: "Changing settings is denied." } });
  });
  try {
    await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
    await card.scrollIntoViewIfNeeded();
    await walk.state("01-auto-default", { visible: [select, card.getByText(/^The order Auto tries/)], hidden: [card.getByRole("status"), card.getByRole("alert")] });
    await expect(select).toHaveValue("");
    await expect(select.locator("option")).toHaveText(["Auto", ...engines.map((e) => `Prefer ${e.label}`), ...engines.map((e) => `Only ${e.label}`)]);
    await walk.state("02-preferred-saved", {
      action: () => select.selectOption(`prefer:${preferred.value}`),
      visible: [select], hidden: [card.getByRole("alert"), card.getByRole("status", { name: "Saving…", exact: true })],
    });
    await expect.poll(async () => (await defaults(request, project.name)).l2_preference).toBe(preferred.value);
    await page.reload();
    await card.scrollIntoViewIfNeeded();
    await walk.state("03-persisted-after-reload", { visible: [select], hidden: [card.getByRole("status", { name: "Saving…", exact: true })] });
    await expect(select).toHaveValue(`prefer:${preferred.value}`);
    await expect(l3).toHaveValue("");
    expect((await defaults(request, project.name)).l3_engine).toBeNull();

    // Only replaces Prefer: both settings are written, so routing never holds both.
    await select.selectOption(`only:${preferred.value}`);
    await expect.poll(async () => {
      const view = await defaults(request, project.name);
      return [view.l2_engine, view.l2_preference];
    }).toEqual([preferred.value, null]);
    await expect(select).toHaveValue(`only:${preferred.value}`);

    deny = true;
    await select.selectOption("");
    await expect(select).toBeDisabled();
    await walk.state("04-saving", { visible: [card.getByRole("status", { name: "Saving…", exact: true })], hidden: [] });
    release();
    await walk.state("05-denied-keeps-saved", {
      visible: [card.getByRole("alert").filter({ hasText: "Changing settings is denied." })],
      hidden: [card.getByRole("status", { name: "Saving…", exact: true })],
    });
    await expect(select).toHaveValue(`only:${preferred.value}`);
    expect((await defaults(request, project.name)).l2_engine).toBe(preferred.value);
    deny = false;
    await walk.state("06-auto-restored", {
      action: () => select.selectOption(""),
      visible: [select], hidden: [card.getByRole("alert"), card.getByRole("status", { name: "Saving…", exact: true })],
    });
    await expect(select).toHaveValue("");
    await expect.poll(async () => {
      const view = await defaults(request, project.name);
      return [view.l2_engine, view.l2_preference, view.l3_engine];
    }).toEqual([null, null, null]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { release(); }
});

test("a routing change made elsewhere is refused and the page shows the current routing", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const { engines } = await defaults(request, project.name);
  expect(engines.length, "The walkthrough needs two configured engines").toBeGreaterThan(1);
  const [elsewhere, here] = engines;
  const card = page.getByRole("main").getByRole("region", { name: "Routing", exact: true });
  const select = card.getByRole("combobox", { name: "L3 routing" });
  await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
  await expect(select).toHaveValue("");
  // Another window keeps L3 on one engine after this page read Auto.
  expect((await request.post("/api/defaults", { data: { project: project.name, setting: "l3_engine", value: elsewhere!.value } })).ok()).toBe(true);
  await walk.state("01-changed-elsewhere", {
    action: () => select.selectOption(`only:${here!.value}`),
    visible: [card.getByRole("alert").filter({ hasText: "Changed in another window. The page shows the current routing." })], hidden: [card.getByRole("status", { name: "Saving…", exact: true })],
  });
  expect((await defaults(request, project.name)).l3_engine).toBe(elsewhere!.value);
  await expect(select).toHaveValue(`only:${elsewhere!.value}`);
});

test("project settings loading and read failure can retry; the project section stays", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const remove = main.getByRole("region", { name: "Project", exact: true }).getByRole("button", { name: "Remove…", exact: true });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let fail = true;
  await page.route(`**/api/defaults/${project.name}`, async (route) => {
    if (!fail) return route.fallback();
    await gate;
    return route.fulfill({ status: 503, json: { error: "Settings unavailable" } });
  });
  try {
    await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
    await walk.state("01-loading", { visible: [main.getByRole("status", { name: "Loading settings…", exact: true })], hidden: [main.getByLabel("Effort").first()] });
    release();
    await walk.state("02-read-error", {
      visible: [main.getByText("Could not load settings."), main.getByRole("button", { name: "Retry", exact: true }), remove],
      hidden: [main.getByRole("status", { name: "Loading settings…", exact: true }), main.getByLabel("Effort").first(), main.getByRole("region", { name: "Routing", exact: true })],
    });
    fail = false;
    await walk.state("03-recovered", {
      action: () => main.getByRole("button", { name: "Retry", exact: true }).click(),
      visible: [main.getByLabel("Effort").first(), main.getByRole("combobox", { name: "L3 routing" }), remove], hidden: [main.getByRole("alert")],
    });
  } finally { release(); }
});

test("saving disables the field and a denied write keeps the saved value", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const engine = role(await defaults(request, project.name), "l2").engines[0]!;
  const group = main.getByRole("group", { name: `Tasks · ${engine.label}`, exact: true });
  const effort = group.getByLabel("Effort");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let deny = true;
  await page.route("**/api/defaults", async (route) => {
    if (!deny) return route.fallback();
    await gate;
    return route.fulfill({ status: 403, json: { error: "Changing settings is denied." } });
  });
  try {
    await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
    await effort.selectOption("native");
    await expect(effort).toBeDisabled();
    await walk.state("01-saving", { visible: [group.getByRole("status", { name: "Saving…", exact: true })], hidden: [] });
    release();
    await walk.state("02-denied", {
      visible: [group.getByText("Changing settings is denied."), group.getByRole("button", { name: "Retry save" })],
      hidden: [group.getByRole("status", { name: "Saving…", exact: true })],
    });
    await expect(effort).toBeEnabled();
    await expect(effort).toHaveValue("");
    expect(values(await defaults(request, project.name))[engine.effort.setting]).toBeNull();
    deny = false;
    await walk.state("03-retry-saved", {
      action: () => group.getByRole("button", { name: "Retry save" }).click(),
      visible: [group.getByRole("status", { name: "Saved.", exact: true })], hidden: [group.getByRole("alert")],
    });
    await expect(effort).toHaveValue("native");
    expect(values(await defaults(request, project.name))[engine.effort.setting]).toBe("native");
  } finally { release(); }
});
